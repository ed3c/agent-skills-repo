from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from skill_arena.roadmap import (
    load_json_object,
    validate_delivery_projection,
    validate_roadmap,
)

ROOT = Path(__file__).resolve().parents[1]
ROADMAP_PATH = ROOT / "data/project/skill-arena-roadmap.json"
SCHEMA_PATH = ROOT / "contracts/skill-arena-roadmap.schema.json"
LANDING_PATH = ROOT / "data/project/landing-evidence.json"


def project_documents() -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    return (
        load_json_object(ROADMAP_PATH),
        load_json_object(SCHEMA_PATH),
        load_json_object(LANDING_PATH),
    )


def item_by_issue(document: dict[str, object], issue: int) -> dict[str, object]:
    items = document["items"]
    assert isinstance(items, list)
    return next(
        item
        for item in items
        if isinstance(item, dict) and item.get("issue") == issue
    )


def test_published_roadmap_passes_contract_and_projection() -> None:
    document, schema, authority = project_documents()
    assert validate_roadmap(document, schema) == []
    assert validate_delivery_projection(document, authority) == []


def test_unknown_dependency_fails_closed() -> None:
    document, schema, _ = project_documents()
    changed = deepcopy(document)
    item_by_issue(changed, 14)["dependencies"] = [12, 13, 999]

    errors = validate_roadmap(changed, schema)

    assert "issue #14: unknown dependency #999" in errors


def test_dependency_cycle_is_reported() -> None:
    document, schema, _ = project_documents()
    changed = deepcopy(document)
    item_by_issue(changed, 12)["status"] = "blocked"
    item_by_issue(changed, 12)["dependencies"] = [13]
    item_by_issue(changed, 13)["status"] = "blocked"
    item_by_issue(changed, 13)["dependencies"] = [12]

    errors = validate_roadmap(changed, schema)

    assert any(error.startswith("dependency cycle: #12 -> #13 -> #12") for error in errors)


def test_done_without_landing_evidence_is_rejected() -> None:
    document, schema, _ = project_documents()
    changed = deepcopy(document)
    item = item_by_issue(changed, 14)
    item["status"] = "done"
    item["evidence"] = "missing"

    errors = validate_roadmap(changed, schema)

    assert any("landing" in error and "required property" in error for error in errors)
    assert any("evidence" in error and "not one of" in error for error in errors)


def test_active_item_cannot_skip_unresolved_dependencies() -> None:
    document, schema, _ = project_documents()
    changed = deepcopy(document)
    dependency = item_by_issue(changed, 12)
    dependency["status"] = "human_gate"
    dependency["evidence"] = "pr"
    dependency.pop("landing")

    errors = validate_roadmap(changed, schema)

    assert "issue #14: status 'ready' while dependencies are unresolved: #12" in errors


def test_projection_rejects_commit_drift() -> None:
    document, _, authority = project_documents()
    changed = deepcopy(document)
    landing = item_by_issue(changed, 12)["landing"]
    assert isinstance(landing, dict)
    landing["commit_sha"] = "f" * 40

    errors = validate_delivery_projection(changed, authority)

    assert any("roadmap landing commit_sha does not match authority" in error for error in errors)


def test_projection_rejects_stale_non_done_state() -> None:
    document, _, authority = project_documents()
    changed = deepcopy(document)
    item = item_by_issue(changed, 12)
    item["status"] = "human_gate"
    item["evidence"] = "pr"
    item.pop("landing")

    errors = validate_delivery_projection(changed, authority)

    assert (
        "issue #12: landing authority is completed but roadmap status is 'human_gate'"
        in errors
    )


def test_projection_rejects_evidence_level_exaggeration() -> None:
    document, _, authority = project_documents()
    changed = deepcopy(authority)
    work_items = changed["work_items"]
    assert isinstance(work_items, list)
    authority_item = next(
        item
        for item in work_items
        if isinstance(item, dict) and item.get("issue_number") == 12
    )
    authority_item["evidence_level"] = "reachable_on_main"

    errors = validate_delivery_projection(document, changed)

    assert any("roadmap evidence does not match authority" in error for error in errors)
