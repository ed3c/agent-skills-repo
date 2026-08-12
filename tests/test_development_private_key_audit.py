from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator

from skill_arena.sandbox_executor.key_audit import (
    KeyAuditError,
    audit_development_private_key,
)
from skill_arena.sandbox_executor.model import load_json_object

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts/development-private-key-audit.schema.json"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def repository(tmp_path: Path, *, reflog: bool = True) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "key-audit@example.invalid")
    git(root, "config", "user.name", "Key Audit")
    if not reflog:
        git(root, "config", "core.logAllRefUpdates", "false")
    (root / "README.md").write_text("key audit fixture\n")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "fixture")
    return root


def private_key(tmp_path: Path) -> tuple[Ed25519PrivateKey, Path]:
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "development.key"
    path.write_bytes(key.private_bytes_raw())
    path.chmod(0o600)
    return key, path


def test_clean_audit_is_reproducible_and_schema_valid(tmp_path: Path) -> None:
    root = repository(tmp_path)
    _, key_path = private_key(tmp_path)

    first, first_refs = audit_development_private_key(root, key_path)
    second, second_refs = audit_development_private_key(root, key_path)

    assert first == second
    assert first_refs == second_refs
    assert first["private_key_absent"] is True
    assert first["git_object_type_counts"]["blob"] == 1
    assert first["git_object_type_counts"]["commit"] == 1
    assert first["git_object_type_counts"]["tree"] == 1
    assert first["scanned_git_objects"] == 3
    assert first["scanned_worktree_files"] == 1
    assert list(Draft202012Validator(load_json_object(SCHEMA)).iter_errors(first)) == []


def test_commit_message_leak_is_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path, reflog=False)
    key, key_path = private_key(tmp_path)
    leaked = key.private_bytes_raw().hex()
    git(root, "commit", "--allow-empty", "-m", leaked)

    with pytest.raises(KeyAuditError, match="commit object"):
        audit_development_private_key(root, key_path)


def test_reflog_message_leak_is_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path)
    key, key_path = private_key(tmp_path)
    leaked = key.private_bytes_raw().hex()
    git(
        root,
        "update-ref",
        "--create-reflog",
        "-m",
        leaked,
        "refs/heads/reflog-fixture",
        "HEAD",
    )

    with pytest.raises(KeyAuditError, match="Git reflog"):
        audit_development_private_key(root, key_path)


def test_annotated_tag_leak_is_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path)
    key, key_path = private_key(tmp_path)
    der = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    git(root, "tag", "-a", "audit-tag", "-m", base64.b64encode(der).decode())

    with pytest.raises(KeyAuditError, match="tag object"):
        audit_development_private_key(root, key_path)


def test_ref_name_leak_is_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path)
    key, key_path = private_key(tmp_path)
    branch = "leak-" + key.private_bytes_raw().hex()
    git(root, "branch", branch)

    with pytest.raises(KeyAuditError, match="Git ref name"):
        audit_development_private_key(root, key_path)


def test_ignored_pem_leak_is_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path)
    key, key_path = private_key(tmp_path)
    (root / ".gitignore").write_text("ignored-development.pem\n")
    git(root, "add", ".gitignore")
    git(root, "commit", "-m", "ignore fixture")
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    (root / "ignored-development.pem").write_bytes(pem)

    with pytest.raises(KeyAuditError, match="worktree file"):
        audit_development_private_key(root, key_path)


def test_untracked_path_name_leak_is_rejected(tmp_path: Path) -> None:
    root = repository(tmp_path)
    key, key_path = private_key(tmp_path)
    leaked_name = key.private_bytes_raw().hex()
    (root / leaked_name).write_text("path-only leak\n")

    with pytest.raises(KeyAuditError, match="worktree path"):
        audit_development_private_key(root, key_path)


def test_key_must_be_outside_repo_and_owner_only(tmp_path: Path) -> None:
    root = repository(tmp_path)
    key = Ed25519PrivateKey.generate()
    inside = root / "inside.key"
    inside.write_bytes(key.private_bytes_raw())
    inside.chmod(0o600)
    with pytest.raises(KeyAuditError, match="outside"):
        audit_development_private_key(root, inside)

    _, outside = private_key(tmp_path)
    outside.chmod(0o644)
    with pytest.raises(KeyAuditError, match="owner-only"):
        audit_development_private_key(root, outside)
