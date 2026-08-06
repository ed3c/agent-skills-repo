"""Zero-print audit for an external development Ed25519 private key.

The audit scans every object reachable from every fetched ref, commit and tag
messages, tree names, the exact ref snapshot, reflog text, and tracked,
untracked, and ignored worktree paths/files. Diagnostics identify only the
object type/id or path; private material is never rendered.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
from collections import Counter
from pathlib import Path
from typing import Sequence

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from skill_arena.core import canonical_bytes

AUDIT_SCHEMA = "development-private-key-audit@1"
_REPRESENTATION_NAMES = (
    "source-bytes",
    "raw-ed25519",
    "pkcs8-der",
    "pkcs8-pem",
    "lower-hex",
    "upper-hex",
    "base64",
    "base64-unpadded",
    "base64url",
    "base64url-unpadded",
)


class KeyAuditError(ValueError):
    """Development-key material may be present or the audit is incomplete."""


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise KeyAuditError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


def _regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise KeyAuditError(f"{label} must be a regular non-symlink file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise KeyAuditError(f"cannot read {label}: {path}: {exc}") from exc


def _load_key(path: Path) -> tuple[Ed25519PrivateKey, bytes]:
    source = _regular_file(path, "development private key")
    try:
        if source.startswith(b"-----BEGIN"):
            value = serialization.load_pem_private_key(source, password=None)
            if not isinstance(value, Ed25519PrivateKey):
                raise TypeError("not Ed25519")
            key = value
        elif len(source) == 32:
            key = Ed25519PrivateKey.from_private_bytes(source)
        else:
            raise ValueError("unexpected key length")
    except (TypeError, ValueError) as exc:
        raise KeyAuditError("development private key is not valid Ed25519 material") from exc
    return key, source.strip()


def _encodings(private_key_path: Path) -> tuple[bytes, ...]:
    key, source = _load_key(private_key_path)
    raw = key.private_bytes_raw()
    der = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).strip()
    values: set[bytes] = {source, raw, der, pem}
    for value in (raw, der, pem):
        values.add(value.hex().encode("ascii"))
        values.add(value.hex().upper().encode("ascii"))
        standard = base64.b64encode(value)
        urlsafe = base64.urlsafe_b64encode(value)
        values.update(
            {
                standard,
                standard.rstrip(b"="),
                urlsafe,
                urlsafe.rstrip(b"="),
            }
        )
    return tuple(sorted((value for value in values if value), key=lambda item: (len(item), item)))


def _contains(data: bytes, representations: Sequence[bytes]) -> bool:
    return any(value in data for value in representations)


def _ref_snapshot(root: Path) -> bytes:
    output = _git(
        root,
        "for-each-ref",
        "--format=%(refname) %(objectname)",
    )
    rows = sorted(row for row in output.splitlines() if row)
    return b"\n".join(rows) + (b"\n" if rows else b"")


def _reflog_snapshot(root: Path) -> bytes:
    output = _git(root, "reflog", "show", "--all", "--format=%H %gD %gs")
    rows = sorted(row for row in output.splitlines() if row)
    return b"\n".join(rows) + (b"\n" if rows else b"")


def audit_development_private_key(
    repo_root: Path | str,
    private_key_path: Path | str,
) -> tuple[dict[str, object], bytes]:
    repository = Path(repo_root).resolve()
    key_path = Path(private_key_path).expanduser().resolve()
    if _git(repository, "rev-parse", "--show-toplevel").decode().strip() != str(
        repository
    ):
        raise KeyAuditError("repo_root is not the repository top level")
    if key_path == repository or repository in key_path.parents:
        raise KeyAuditError("development private key must live outside the repository")
    mode = stat.S_IMODE(key_path.stat().st_mode) if key_path.exists() else 0
    if mode & 0o077:
        raise KeyAuditError("development private key permissions must be owner-only")
    representations = _encodings(key_path)

    refs = _ref_snapshot(repository)
    if _contains(refs, representations):
        raise KeyAuditError("development key material appears in a Git ref name")
    reflog = _reflog_snapshot(repository)
    if _contains(reflog, representations):
        raise KeyAuditError("development key material appears in a Git reflog")

    object_lines = _git(
        repository,
        "rev-list",
        "--objects",
        "--all",
        "HEAD",
    ).splitlines()
    object_ids = sorted(
        {line.split(maxsplit=1)[0].decode("ascii") for line in object_lines if line}
    )
    process = subprocess.Popen(
        ["git", "-C", str(repository), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdin is None or process.stdout is None:
        process.kill()
        raise KeyAuditError("git cat-file pipes are unavailable")
    counts: Counter[str] = Counter()
    try:
        for object_id in object_ids:
            process.stdin.write(object_id.encode("ascii") + b"\n")
            process.stdin.flush()
            header = process.stdout.readline()
            parts = header.rstrip(b"\n").split()
            if len(parts) == 2 and parts[1] == b"missing":
                raise KeyAuditError(f"Git object is missing: {object_id}")
            if len(parts) != 3:
                raise KeyAuditError("git cat-file returned an invalid header")
            object_type = parts[1].decode("ascii", errors="replace")
            try:
                size = int(parts[2])
            except ValueError as exc:
                raise KeyAuditError("git cat-file returned an invalid size") from exc
            data = process.stdout.read(size)
            delimiter = process.stdout.read(1)
            if len(data) != size or delimiter != b"\n":
                raise KeyAuditError("git cat-file returned a truncated object")
            counts[object_type] += 1
            if _contains(data, representations):
                raise KeyAuditError(
                    "development key material appears in Git "
                    f"{object_type} object {object_id}"
                )
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait(timeout=5)
            raise KeyAuditError("git cat-file did not terminate") from exc
    if process.returncode != 0:
        stderr = (process.stderr.read() if process.stderr else b"").decode(
            "utf-8", errors="replace"
        )
        raise KeyAuditError(f"git cat-file failed: {stderr.strip()}")

    worktree_paths: set[bytes] = set()
    for arguments in (
        ("ls-files", "-co", "--exclude-standard", "-z"),
        ("ls-files", "-oi", "--exclude-standard", "-z"),
    ):
        worktree_paths.update(
            value for value in _git(repository, *arguments).split(b"\0") if value
        )
    scanned_worktree_files = 0
    for raw_path in sorted(worktree_paths):
        if _contains(raw_path, representations):
            raise KeyAuditError("development key material appears in a worktree path")
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        path = repository / relative
        if path.is_symlink() or not path.is_file():
            continue
        scanned_worktree_files += 1
        if _contains(path.read_bytes(), representations):
            raise KeyAuditError(
                f"development key material appears in worktree file {relative}"
            )

    head = _git(repository, "rev-parse", "HEAD").decode("ascii").strip()
    audit: dict[str, object] = {
        "schema_version": AUDIT_SCHEMA,
        "repository_head": head,
        "refs_digest": _sha256(refs),
        "refs_count": len(refs.splitlines()),
        "reflog_digest": _sha256(reflog),
        "reflog_entry_count": len(reflog.splitlines()),
        "scanned_git_objects": sum(counts.values()),
        "git_object_type_counts": dict(sorted(counts.items())),
        "scanned_worktree_files": scanned_worktree_files,
        "representations_checked": list(_REPRESENTATION_NAMES),
        "private_key_absent": True,
    }
    audit["audit_digest"] = _sha256(canonical_bytes(audit))
    return audit, refs


def write_key_audit(
    output_path: Path | str,
    refs_path: Path | str,
    audit: dict[str, object],
    refs: bytes,
) -> None:
    output = Path(output_path)
    refs_output = Path(refs_path)
    if output.exists() or refs_output.exists():
        raise KeyAuditError("key-audit output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    refs_output.parent.mkdir(parents=True, exist_ok=True)
    temporary_json = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary_refs = refs_output.with_name(f".{refs_output.name}.{os.getpid()}.tmp")
    try:
        temporary_json.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary_refs.write_bytes(refs)
        os.replace(temporary_json, output)
        os.replace(temporary_refs, refs_output)
    finally:
        temporary_json.unlink(missing_ok=True)
        temporary_refs.unlink(missing_ok=True)
