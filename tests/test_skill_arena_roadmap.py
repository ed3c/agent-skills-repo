from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from skill_arena.roadmap import load_json_object, validate_roadmap

ROOT = Path(__file__).resolve().parents[1]
ROADMAP_PATH = ROOT / "data/project/skill-arena-roadmap.json"
SCHEMA_PATH = ROOT / "contracts/skill-arena-roadmap.schema.json"


def roadmap_and_schema() -> tuple[dict[str, object], dict[str, object]]:
    return load_json_object(ROADMAP_PATH), load_json_object(SCHEMA_PATH)


def item_by_issue(document: dict[str, object], issue: int) -> dict[str, object]:
    items = document["items"]
    assert isinstance(items, list)
    return next(
        item
        for item in items
        if isinstance(item, dict) and item.get("issue") == issue
    )


def test_published_roadmap_passes_contract() -> None:
    document, schema = roadmap_and_schema()
    assert validate_roadmap(document, schema) == []


def test_unknown_dependency_fails_closed() -> None:
    document, schema = roadmap_and_schema()
    changed = deepcopy(document)
    item_by_issue(changed, 14)["dependencies"] = [12, 13, 999]

    errors = validate_roadmap(changed, schema)

    assert "issue #14: unknown dependency #999" in errors


def test_dependency_cycle_is_reported() -> None:
    document, schema = roadmap_and_schema()
    changed = deepcopy(document)
    item_by_issue(changed, 12)["status"] = "blocked"
    item_by_issue(changed, 12)["dependencies"] = [13]
    item_by_issue(changed, 13)["status"] = "blocked"
    item_by_issue(changed, 13)["dependencies"] = [12]

    errors = validate_roadmap(changed, schema)

    assert any(error.startswith("dependency cycle: #12 -> #13 -> #12") for error in errors)


def test_done_without_landing_evidence_is_rejected() -> None:
    document, schema = roadmap_and_schema()
    changed = deepcopy(document)
    item_by_issue(changed, 12)["status"] = "done"
    item_by_issue(changed, 12)["evidence"] = "missing"

    errors = validate_roadmap(changed, schema)

    assert any("landing" in error and "required property" in error for error in errors)
    assert any("evidence" in error and "not one of" in error for error in errors)


def test_active_item_cannot_skip_unresolved_dependencies() -> None:
    document, schema = roadmap_and_schema()
    changed = deepcopy(document)
    item_by_issue(changed, 14)["status"] = "ready"

    errors = validate_roadmap(changed, schema)

    assert (
        "issue #14: status 'ready' while dependencies are unresolved: #12, #13"
        in errors
    )
