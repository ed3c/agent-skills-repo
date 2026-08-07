from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from skill_arena.landing_evidence import LandingEvidenceError
from skill_arena.landing_evidence_fragments import load_landing_evidence_bundle

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/check_landing_evidence.py"
SCHEMA = ROOT / "contracts/landing-evidence.schema.json"


def base_manifest() -> dict[str, object]:
    return {
        "schema_version": "landing-evidence@1",
        "repository": "ed3c/agent-skills-repo",
        "authority": {
            "default_branch": "main",
            "completion_rule": "reachable-commit-plus-digested-paths-and-tests",
        },
        "work_items": [
            {
                "issue_number": 3,
                "title": "stale",
                "status": "pending",
                "evidence_level": "missing",
                "notes": "stale",
            }
        ],
    }


def fragment(issue: int, *, title: str = "replacement") -> dict[str, object]:
    return {
        "schema_version": "landing-evidence-fragment@1",
        "repository": "ed3c/agent-skills-repo",
        "operation": "upsert",
        "work_item": {
            "issue_number": issue,
            "title": title,
            "status": "blocked",
            "evidence_level": "reachable_on_main",
            "notes": "partial delivery only",
        },
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_fragments_replace_stale_items_append_new_items_and_sort(tmp_path: Path) -> None:
    manifest = tmp_path / "landing.json"
    fragments = tmp_path / "fragments"
    write_json(manifest, base_manifest())
    write_json(fragments / "35.json", fragment(35, title="new"))
    write_json(fragments / "03.json", fragment(3, title="corrected"))

    merged = load_landing_evidence_bundle(manifest, fragments)
    items = merged["work_items"]
    assert [item["issue_number"] for item in items] == [3, 35]
    assert items[0]["title"] == "corrected"
    assert items[1]["title"] == "new"


def test_multiple_fragments_cannot_target_one_issue(tmp_path: Path) -> None:
    manifest = tmp_path / "landing.json"
    fragments = tmp_path / "fragments"
    write_json(manifest, base_manifest())
    write_json(fragments / "a.json", fragment(3))
    write_json(fragments / "b.json", fragment(3))
    with pytest.raises(LandingEvidenceError, match="multiple.*issue #3"):
        load_landing_evidence_bundle(manifest, fragments)


def test_fragment_repository_mismatch_is_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "landing.json"
    fragments = tmp_path / "fragments"
    value = fragment(35)
    value["repository"] = "other/repo"
    write_json(manifest, base_manifest())
    write_json(fragments / "35.json", value)
    with pytest.raises(LandingEvidenceError, match="repository mismatch"):
        load_landing_evidence_bundle(manifest, fragments)


def test_symlinked_fragment_is_rejected(tmp_path: Path) -> None:
    manifest = tmp_path / "landing.json"
    fragments = tmp_path / "fragments"
    outside = tmp_path / "outside.json"
    write_json(manifest, base_manifest())
    write_json(outside, fragment(35))
    fragments.mkdir()
    (fragments / "35.json").symlink_to(outside)
    with pytest.raises(LandingEvidenceError, match="regular non-symlink"):
        load_landing_evidence_bundle(manifest, fragments)


def test_cli_applies_default_style_fragments_before_schema_validation(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "landing.json"
    fragments = tmp_path / "fragments"
    write_json(manifest, base_manifest())
    write_json(fragments / "03.json", fragment(3))
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--manifest",
            str(manifest),
            "--fragments-dir",
            str(fragments),
            "--schema",
            str(SCHEMA),
            "--repo-root",
            str(ROOT),
            "--no-git",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "digest-only" in completed.stdout
