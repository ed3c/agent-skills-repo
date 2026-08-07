"""Deterministic additive/upsert fragments for landing-evidence authority.

The original monolithic manifest remains valid. Fragments let later delivery
slices add or correct one work item without rewriting unrelated historical
records. The merged in-memory manifest is still validated by the existing
``landing-evidence@1`` schema and Git-history checks.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

from skill_arena.landing_evidence import LandingEvidenceError

FRAGMENT_SCHEMA_VERSION = "landing-evidence-fragment@1"
_FRAGMENT_FIELDS = {"schema_version", "repository", "operation", "work_item"}


def _load_json_object(path: Path, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise LandingEvidenceError(f"{label} must be a regular non-symlink file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LandingEvidenceError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LandingEvidenceError(f"{label} root must be an object: {path}")
    return value


def load_landing_evidence_bundle(
    manifest_path: Path | str,
    fragments_dir: Path | str | None,
) -> dict[str, object]:
    """Load a base manifest and apply sorted one-item upsert fragments.

    A given issue may appear in at most one fragment. This prevents filesystem
    ordering from becoming an authority decision. Fragment work-item structure
    is validated later by the existing full-manifest schema.
    """
    base_path = Path(manifest_path)
    base = _load_json_object(base_path, "landing-evidence manifest")
    repository = base.get("repository")
    work_items = base.get("work_items")
    if not isinstance(repository, str) or not repository:
        raise LandingEvidenceError("landing-evidence repository is missing")
    if not isinstance(work_items, list) or not all(
        isinstance(item, dict) for item in work_items
    ):
        raise LandingEvidenceError("landing-evidence work_items must be objects")

    merged = copy.deepcopy(base)
    merged_items = cast(list[dict[str, object]], merged["work_items"])
    index: dict[int, int] = {}
    for position, item in enumerate(merged_items):
        issue = item.get("issue_number")
        if type(issue) is not int:
            raise LandingEvidenceError("base work item issue_number is invalid")
        if cast(int, issue) in index:
            raise LandingEvidenceError("duplicate issue_number in base landing evidence")
        index[cast(int, issue)] = position

    if fragments_dir is None:
        merged_items.sort(key=lambda item: cast(int, item["issue_number"]))
        return merged
    directory = Path(fragments_dir)
    if not directory.exists():
        merged_items.sort(key=lambda item: cast(int, item["issue_number"]))
        return merged
    if directory.is_symlink() or not directory.is_dir():
        raise LandingEvidenceError(
            f"landing-evidence fragments path must be a directory: {directory}"
        )

    fragment_issues: set[int] = set()
    for path in sorted(directory.glob("*.json")):
        fragment = _load_json_object(path, "landing-evidence fragment")
        if set(fragment) != _FRAGMENT_FIELDS:
            raise LandingEvidenceError(
                f"landing-evidence fragment fields mismatch: {path}"
            )
        if fragment.get("schema_version") != FRAGMENT_SCHEMA_VERSION:
            raise LandingEvidenceError(
                f"landing-evidence fragment schema_version is invalid: {path}"
            )
        if fragment.get("repository") != repository:
            raise LandingEvidenceError(
                f"landing-evidence fragment repository mismatch: {path}"
            )
        if fragment.get("operation") != "upsert":
            raise LandingEvidenceError(
                f"landing-evidence fragment operation must be 'upsert': {path}"
            )
        work_item = fragment.get("work_item")
        if not isinstance(work_item, dict):
            raise LandingEvidenceError(
                f"landing-evidence fragment work_item must be an object: {path}"
            )
        issue = work_item.get("issue_number")
        if type(issue) is not int or cast(int, issue) < 1:
            raise LandingEvidenceError(
                f"landing-evidence fragment issue_number is invalid: {path}"
            )
        issue_number = cast(int, issue)
        if issue_number in fragment_issues:
            raise LandingEvidenceError(
                f"multiple landing-evidence fragments target issue #{issue_number}"
            )
        fragment_issues.add(issue_number)
        replacement = copy.deepcopy(cast(dict[str, object], work_item))
        if issue_number in index:
            merged_items[index[issue_number]] = replacement
        else:
            index[issue_number] = len(merged_items)
            merged_items.append(replacement)

    merged_items.sort(key=lambda item: cast(int, item["issue_number"]))
    return merged
