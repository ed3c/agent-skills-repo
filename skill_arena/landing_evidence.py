"""Machine-verifiable authority for roadmap completion claims.

Issue comments and dashboard fields coordinate work; they are not delivery
proof. A completed item must bind a full commit SHA reachable from the declared
main ref, an exact changed-path set, and digested test evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Mapping, Sequence, cast

from jsonschema import Draft202012Validator

FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class LandingEvidenceError(ValueError):
    """The landing-evidence authority is malformed or makes an invalid claim."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def changed_paths_digest(paths: Sequence[str]) -> str:
    return sha256_digest({"paths": sorted(paths)})


def compute_test_evidence_digest(evidence: Mapping[str, object]) -> str:
    payload = {key: value for key, value in evidence.items() if key != "digest"}
    return sha256_digest(payload)


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_git_bytes(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        check=False,
    )


def resolve_main_ref(root: Path, requested: str | None = None) -> str:
    candidates = [requested] if requested else ["origin/main", "main"]
    for candidate in candidates:
        if not candidate:
            continue
        result = _run_git(root, "rev-parse", "--verify", f"{candidate}^{{commit}}")
        if result.returncode == 0:
            return candidate
    rendered = requested or "origin/main or main"
    raise LandingEvidenceError(f"declared main ref is unavailable: {rendered}")


def actual_changed_paths(root: Path, commit_sha: str) -> list[str]:
    """Return the exact path set introduced by a commit against its first parent.

    Pull-request merge commits are compared with their first parent, which is the
    target branch tip. Root commits use ``diff-tree --root``. Rename detection is
    disabled so the evidence set remains deterministic: a rename is represented
    by the old and new paths rather than a similarity-score-dependent rename.
    """
    lineage = _run_git(root, "rev-list", "--parents", "-n", "1", commit_sha)
    if lineage.returncode != 0:
        raise LandingEvidenceError(
            f"cannot inspect completed commit parents: {commit_sha}:"
            f" {lineage.stderr.strip()}"
        )
    tokens = lineage.stdout.strip().split()
    if not tokens or tokens[0] != commit_sha:
        raise LandingEvidenceError(
            f"cannot resolve completed commit lineage: {commit_sha}"
        )

    if len(tokens) == 1:
        result = _run_git_bytes(
            root,
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            "--no-renames",
            "-z",
            commit_sha,
        )
    else:
        result = _run_git_bytes(
            root,
            "diff",
            "--name-only",
            "--no-renames",
            "-z",
            tokens[1],
            commit_sha,
        )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise LandingEvidenceError(
            f"cannot inspect completed commit paths: {commit_sha}: {stderr}"
        )

    try:
        paths = [
            entry.decode("utf-8")
            for entry in result.stdout.split(b"\0")
            if entry
        ]
    except UnicodeDecodeError as exc:
        raise LandingEvidenceError(
            f"completed commit contains a non-UTF-8 repository path: {commit_sha}"
        ) from exc
    return sorted(paths)


def _schema_errors(manifest: object, schema: object) -> list[str]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(manifest),
        key=lambda error: list(error.absolute_path),
    )
    rendered: list[str] = []
    for error in errors:
        path = ".".join(str(part) for part in error.absolute_path) or "<root>"
        if path.endswith("commit_sha"):
            rendered.append(
                f"{path}: completed commit must be a full 40-character lowercase SHA"
            )
        else:
            rendered.append(f"{path}: {error.message}")
    return rendered


def validate_landing_evidence(
    manifest: object,
    schema: object,
    *,
    repo_root: Path | str,
    main_ref: str | None = None,
    verify_git: bool = True,
) -> None:
    errors = _schema_errors(manifest, schema)
    if errors:
        raise LandingEvidenceError("schema validation failed: " + "; ".join(errors))

    root = Path(repo_root)
    data = cast(dict[str, object], manifest)
    items = cast(list[dict[str, object]], data["work_items"])
    issue_numbers = [cast(int, item["issue_number"]) for item in items]
    if len(issue_numbers) != len(set(issue_numbers)):
        raise LandingEvidenceError("duplicate issue_number in landing evidence")

    resolved_main = resolve_main_ref(root, main_ref) if verify_git else None
    for item in items:
        issue = cast(int, item["issue_number"])
        if item["status"] != "completed":
            continue
        landing = cast(dict[str, object], item["landing"])
        commit_sha = cast(str, landing["commit_sha"])
        if not FULL_SHA_RE.fullmatch(commit_sha):
            raise LandingEvidenceError(
                f"issue #{issue} completed commit must be a full 40-character SHA:"
                f" {commit_sha!r}"
            )

        paths = cast(list[str], landing["changed_paths"])
        if paths != sorted(paths):
            raise LandingEvidenceError(
                f"issue #{issue} changed_paths must be sorted for canonical review"
            )
        expected_paths_digest = changed_paths_digest(paths)
        if landing["changed_paths_digest"] != expected_paths_digest:
            raise LandingEvidenceError(
                f"issue #{issue} changed_paths_digest mismatch:"
                f" expected {expected_paths_digest}"
            )

        test_evidence = cast(dict[str, object], landing["test_evidence"])
        expected_test_digest = compute_test_evidence_digest(test_evidence)
        if test_evidence["digest"] != expected_test_digest:
            raise LandingEvidenceError(
                f"issue #{issue} test evidence digest mismatch:"
                f" expected {expected_test_digest}"
            )

        if not verify_git:
            continue
        exists = _run_git(root, "cat-file", "-e", f"{commit_sha}^{{commit}}")
        if exists.returncode != 0:
            raise LandingEvidenceError(
                f"issue #{issue} completed commit does not exist: {commit_sha}"
            )
        ancestor = _run_git(
            root, "merge-base", "--is-ancestor", commit_sha, cast(str, resolved_main)
        )
        if ancestor.returncode != 0:
            raise LandingEvidenceError(
                f"issue #{issue} completed commit is not reachable from"
                f" {resolved_main}: {commit_sha}"
            )

        observed_paths = actual_changed_paths(root, commit_sha)
        if paths != observed_paths:
            raise LandingEvidenceError(
                f"issue #{issue} changed_paths do not match commit {commit_sha}:"
                f" declared={paths!r} actual={observed_paths!r}"
            )
