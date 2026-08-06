from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from skill_arena.landing_evidence import (
    LandingEvidenceError,
    changed_paths_digest,
    compute_test_evidence_digest,
    validate_landing_evidence,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "contracts/landing-evidence.schema.json").read_text(encoding="utf-8")
)


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "arena@example.invalid")
    git(root, "config", "user.name", "Arena Test")
    (root / "README.md").write_text("base\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "base")
    return root, git(root, "rev-parse", "HEAD")


def completed_manifest(commit_sha: str) -> dict[str, object]:
    paths = ["README.md", "tests/test_landing_evidence.py"]
    evidence: dict[str, object] = {
        "source": "local_reproduction",
        "commands": ["python -m pytest -q tests/test_landing_evidence.py"],
        "result_summary": "6 passed",
        "independently_verified": True,
    }
    evidence["digest"] = compute_test_evidence_digest(evidence)
    return {
        "schema_version": "landing-evidence@1",
        "repository": "ed3c/agent-skills-repo",
        "authority": {
            "default_branch": "main",
            "completion_rule": "reachable-commit-plus-digested-paths-and-tests",
        },
        "work_items": [
            {
                "issue_number": 12,
                "title": "truth gate",
                "status": "completed",
                "evidence_level": "independently_verified",
                "notes": "test fixture",
                "landing": {
                    "commit_sha": commit_sha,
                    "pull_request": 20,
                    "changed_paths": paths,
                    "changed_paths_digest": changed_paths_digest(paths),
                    "test_evidence": evidence,
                },
            }
        ],
    }


def test_reachable_completed_item_passes(tmp_path: Path) -> None:
    root, commit_sha = repository(tmp_path)
    validate_landing_evidence(
        completed_manifest(commit_sha), SCHEMA, repo_root=root, main_ref="main"
    )


def test_short_fabricated_sha_is_rejected_with_specific_diagnostic(tmp_path: Path) -> None:
    root, commit_sha = repository(tmp_path)
    manifest = completed_manifest(commit_sha)
    manifest["work_items"][0]["landing"]["commit_sha"] = "2e5a0c7"  # type: ignore[index]
    with pytest.raises(LandingEvidenceError, match="full 40-character"):
        validate_landing_evidence(manifest, SCHEMA, repo_root=root, main_ref="main")


def test_unknown_full_sha_is_rejected(tmp_path: Path) -> None:
    root, _ = repository(tmp_path)
    manifest = completed_manifest("f" * 40)
    with pytest.raises(LandingEvidenceError, match="does not exist"):
        validate_landing_evidence(manifest, SCHEMA, repo_root=root, main_ref="main")


def test_side_branch_commit_is_not_main_reachable(tmp_path: Path) -> None:
    root, main_sha = repository(tmp_path)
    git(root, "checkout", "-b", "side")
    (root / "side.txt").write_text("side\n", encoding="utf-8")
    git(root, "add", "side.txt")
    git(root, "commit", "-m", "side")
    side_sha = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "main")
    assert git(root, "rev-parse", "HEAD") == main_sha
    with pytest.raises(LandingEvidenceError, match="not reachable"):
        validate_landing_evidence(
            completed_manifest(side_sha), SCHEMA, repo_root=root, main_ref="main"
        )


def test_digest_tampering_is_rejected(tmp_path: Path) -> None:
    root, commit_sha = repository(tmp_path)
    manifest = completed_manifest(commit_sha)
    manifest["work_items"][0]["landing"]["changed_paths_digest"] = (  # type: ignore[index]
        "sha256:" + "0" * 64
    )
    with pytest.raises(LandingEvidenceError, match="changed_paths_digest mismatch"):
        validate_landing_evidence(manifest, SCHEMA, repo_root=root, main_ref="main")


def test_duplicate_issue_numbers_are_rejected(tmp_path: Path) -> None:
    root, commit_sha = repository(tmp_path)
    manifest = completed_manifest(commit_sha)
    manifest["work_items"].append(dict(manifest["work_items"][0]))  # type: ignore[index]
    with pytest.raises(LandingEvidenceError, match="duplicate issue_number"):
        validate_landing_evidence(manifest, SCHEMA, repo_root=root, main_ref="main")
