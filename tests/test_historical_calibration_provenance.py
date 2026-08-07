from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from skill_arena.calibration_provenance import (
    load_json_object,
    manifest_digest,
    validate_historical_calibration,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data/calibration/historical/skill-bettor-4b2de858.json"
SCHEMA = ROOT / "contracts/historical-calibration-provenance.schema.json"


def documents() -> tuple[dict[str, object], dict[str, object]]:
    return load_json_object(MANIFEST), load_json_object(SCHEMA)


def resign(document: dict[str, object]) -> None:
    document["manifest_digest"] = manifest_digest(document)


def test_published_historical_provenance_passes() -> None:
    document, schema = documents()
    assert validate_historical_calibration(document, schema) == []


def test_historical_artifact_cannot_become_qualification_authority() -> None:
    document, schema = documents()
    changed = deepcopy(document)
    changed["authority"]["qualification_eligible"] = True
    resign(changed)

    errors = validate_historical_calibration(changed, schema)

    assert any("qualification_eligible" in error for error in errors)


def test_cost_vector_is_recomputed_with_decimal_arithmetic() -> None:
    document, schema = documents()
    changed = deepcopy(document)
    changed["stage2_report"]["case_costs_usd"][0] = "0.1253506"
    resign(changed)

    errors = validate_historical_calibration(changed, schema)

    assert "historical budget does not recompute: cost_total_usd" in errors
    assert "recomputed budget row is stale: cost_total_usd" in errors


def test_success_and_latency_are_recomputed_from_report_inputs() -> None:
    document, schema = documents()
    changed = deepcopy(document)
    changed["stage2_report"]["passed_count"] = 8
    changed["stage2_report"]["max_observed_agent_wall_ms"] = 42000
    resign(changed)

    errors = validate_historical_calibration(changed, schema)

    assert any("passed_count" in error for error in errors)
    assert any("max_observed_agent_wall_ms" in error for error in errors)


def test_recovered_and_unrecoverable_paths_cannot_overlap() -> None:
    document, schema = documents()
    changed = deepcopy(document)
    changed["unrecoverable_tree_paths"][0]["path"] = "calibration/core.py"
    changed["unrecoverable_tree_paths"] = sorted(
        changed["unrecoverable_tree_paths"], key=lambda row: row["path"]
    )
    resign(changed)

    errors = validate_historical_calibration(changed, schema)

    assert any("both recovered and unrecoverable" in error for error in errors)


def test_manifest_digest_tampering_is_rejected() -> None:
    document, schema = documents()
    changed = deepcopy(document)
    changed["known_deviations"][0]["historical_value"] += " tampered"

    errors = validate_historical_calibration(changed, schema)

    assert any("manifest_digest mismatch" in error for error in errors)
