"""Validation for historical calibration provenance that is never authority."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping, cast

from jsonschema import Draft202012Validator

from skill_arena.core import canonical_bytes

SCHEMA_VERSION = "historical-calibration-provenance@1"


class CalibrationProvenanceError(ValueError):
    """Historical provenance is malformed or could be mistaken for authority."""


def load_json_object(path: Path | str) -> dict[str, object]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CalibrationProvenanceError(
            f"cannot read JSON object {source}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise CalibrationProvenanceError(f"JSON root must be an object: {source}")
    return cast(dict[str, object], value)


def manifest_digest(document: Mapping[str, object]) -> str:
    payload = {
        key: value for key, value in document.items() if key != "manifest_digest"
    }
    return "sha256:" + hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _schema_path(error: object) -> str:
    parts = [str(part) for part in error.absolute_path]  # type: ignore[attr-defined]
    return ".".join(parts) if parts else "<root>"


def _sorted_unique_paths(
    rows: object,
    *,
    label: str,
) -> tuple[list[str], list[Mapping[str, object]]]:
    if not isinstance(rows, list):
        raise CalibrationProvenanceError(f"{label} must be a list")
    mappings: list[Mapping[str, object]] = []
    paths: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise CalibrationProvenanceError(f"{label}[{index}] must be an object")
        path = row.get("path")
        if not isinstance(path, str) or not path:
            raise CalibrationProvenanceError(f"{label}[{index}].path is invalid")
        mappings.append(row)
        paths.append(path)
    if paths != sorted(paths):
        raise CalibrationProvenanceError(f"{label} paths must be sorted")
    if len(paths) != len(set(paths)):
        raise CalibrationProvenanceError(f"{label} paths must be unique")
    return paths, mappings


def validate_historical_calibration(
    document: Mapping[str, object],
    schema: Mapping[str, object],
) -> list[str]:
    errors = [
        f"schema {_schema_path(error)}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema).iter_errors(document),
            key=lambda error: (list(error.absolute_path), error.message),
        )
    ]
    if errors:
        return sorted(set(errors))

    authority = cast(Mapping[str, object], document["authority"])
    forbidden_true = {
        "delivery_authority",
        "qualification_eligible",
        "preregistration_eligible",
        "runtime_import_allowed",
    }
    for field in sorted(forbidden_true):
        if authority.get(field) is not False:
            errors.append(f"authority.{field} must remain false")
    if authority.get("status") != "historical-unlanded":
        errors.append("authority.status must remain historical-unlanded")

    source = cast(Mapping[str, object], document["source"])
    prefix = cast(str, source["target_prefix"])
    recovered_paths, recovered = _sorted_unique_paths(
        document["recovered_files"], label="recovered_files"
    )
    blobs: list[str] = []
    for row in recovered:
        path = cast(str, row["path"])
        source_path = row.get("source_path")
        if source_path != prefix + path:
            errors.append(
                f"recovered file source_path does not match target prefix: {path}"
            )
        blob = cast(str, row["git_blob_sha1"])
        blobs.append(blob)
    if len(blobs) != len(set(blobs)):
        errors.append("recovered Git blob identities must be unique")

    missing_paths, _ = _sorted_unique_paths(
        document["unrecoverable_tree_paths"], label="unrecoverable_tree_paths"
    )
    overlap = sorted(set(recovered_paths) & set(missing_paths))
    if overlap:
        errors.append(f"paths cannot be both recovered and unrecoverable: {overlap}")

    report = cast(Mapping[str, object], document["stage2_report"])
    costs = report["case_costs_usd"]
    if not isinstance(costs, list):
        errors.append("stage2_report.case_costs_usd must be a list")
        decimal_total = Decimal("NaN")
    else:
        try:
            decimal_total = sum((Decimal(cast(str, value)) for value in costs), Decimal(0))
        except (InvalidOperation, TypeError) as exc:
            errors.append(f"stage2 cost vector is not decimal-safe: {exc}")
            decimal_total = Decimal("NaN")
    case_count = cast(int, report["case_count"])
    passed_count = cast(int, report["passed_count"])
    max_wall_ms = cast(int, report["max_observed_agent_wall_ms"])
    if isinstance(costs, list) and len(costs) != case_count:
        errors.append("stage2 cost count does not equal case_count")
    if not 0 <= passed_count <= case_count:
        errors.append("stage2 passed_count is outside 0..case_count")

    recomputed_success = passed_count * 1_000_000 // case_count
    recomputed_latency = max_wall_ms * 3 // 2
    recomputed_cost = format(decimal_total, "f")
    budgets = cast(Mapping[str, object], document["historical_budgets"])
    recomputed = cast(Mapping[str, object], budgets["recomputed"])
    expected = {
        "target_success_ppm": recomputed_success,
        "latency_budget_ms": recomputed_latency,
        "cost_total_usd": recomputed_cost,
    }
    for field, value in expected.items():
        if budgets.get(field) != value:
            errors.append(f"historical budget does not recompute: {field}")
        if recomputed.get(field) != value:
            errors.append(f"recomputed budget row is stale: {field}")
    if recomputed.get("all_equal") is not True:
        errors.append("historical budget recomputation must record all_equal=true")

    deviations = document["known_deviations"]
    if isinstance(deviations, list):
        codes = [
            row.get("code")
            for row in deviations
            if isinstance(row, Mapping) and isinstance(row.get("code"), str)
        ]
        if len(codes) != len(deviations) or len(codes) != len(set(codes)):
            errors.append("known deviation codes must be present and unique")

    claimed_digest = document.get("manifest_digest")
    expected_digest = manifest_digest(document)
    if claimed_digest != expected_digest:
        errors.append(
            f"manifest_digest mismatch: expected {expected_digest}, got {claimed_digest}"
        )
    return sorted(set(errors))


def validate_historical_calibration_files(
    manifest_path: Path | str,
    schema_path: Path | str,
) -> list[str]:
    return validate_historical_calibration(
        load_json_object(manifest_path),
        load_json_object(schema_path),
    )
