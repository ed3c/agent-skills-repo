"""Bind immutable environment-image identity into SkillsBench execution parity.

The initial execution-evidence schema required ``environment_image_digest``,
while the reviewed extractor and parity binder still emitted/accepted the older
three-field execution envelope. This module closes that split without weakening
the schema:

- load and validate the independently recorded source/normalized image IDs;
- enrich each execution-evidence document and recompute its digest;
- require both enriched documents to bind the same immutable image;
- adapt through the legacy semantic binder, then restore the enriched evidence
  identities in the public parity report.
"""
from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Mapping, cast

from .common import canonical_bytes, read_json_object, sha256_bytes
from .models import SkillsBenchAdapterError
from .parity import bind_execution_parity, report_digest

_IMAGE_SCHEMA = "skillsbench-environment-images@1"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_FIELDS = {
    "schema_version",
    "source_image_id",
    "normalized_image_id",
    "identical",
    "docker_server_version",
    "network_mode",
}
_LEGACY_EXECUTION_FIELDS = {"benchflow_version", "agent", "sandbox"}
_ENRICHED_EXECUTION_FIELDS = {
    "benchflow_version",
    "agent",
    "sandbox",
    "environment_image_digest",
}


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise SkillsBenchAdapterError(f"{label} must be an immutable sha256 digest")
    return value


def _validate_evidence_digest(value: Mapping[str, object], label: str) -> None:
    claimed = value.get("evidence_digest")
    expected = sha256_bytes(
        canonical_bytes(
            {key: item for key, item in value.items() if key != "evidence_digest"}
        )
    )
    if claimed != expected:
        raise SkillsBenchAdapterError(f"{label} evidence_digest mismatch")


def load_environment_image_identity(path: Path | str) -> dict[str, object]:
    value = read_json_object(path)
    if set(value) != _IMAGE_FIELDS:
        raise SkillsBenchAdapterError(
            "environment image identity fields differ: "
            f"expected={sorted(_IMAGE_FIELDS)} actual={sorted(value)}"
        )
    if value.get("schema_version") != _IMAGE_SCHEMA:
        raise SkillsBenchAdapterError("environment image identity schema is unsupported")
    source = _digest(value.get("source_image_id"), "source environment image")
    normalized = _digest(
        value.get("normalized_image_id"), "normalized environment image"
    )
    if value.get("identical") is not True or source != normalized:
        raise SkillsBenchAdapterError(
            "source and normalized environment image identities differ"
        )
    docker_version = value.get("docker_server_version")
    if not isinstance(docker_version, str) or not docker_version:
        raise SkillsBenchAdapterError("Docker server version is absent")
    if value.get("network_mode") not in {"public", "no-network"}:
        raise SkillsBenchAdapterError("environment image network_mode is invalid")
    return value


def attach_environment_image_identity(
    evidence: Mapping[str, object],
    *,
    image_identity_path: Path | str,
    surface: str,
) -> dict[str, object]:
    if surface not in {"upstream", "normalized"}:
        raise SkillsBenchAdapterError(f"execution evidence surface is invalid: {surface!r}")
    _validate_evidence_digest(evidence, f"{surface} evidence before image binding")
    execution = evidence.get("execution")
    if not isinstance(execution, Mapping):
        raise SkillsBenchAdapterError("execution evidence execution envelope is absent")
    if set(execution) != _LEGACY_EXECUTION_FIELDS:
        raise SkillsBenchAdapterError(
            "execution envelope cannot be image-bound from an unknown shape: "
            f"actual={sorted(execution)}"
        )
    identity = load_environment_image_identity(image_identity_path)
    field = "source_image_id" if surface == "upstream" else "normalized_image_id"
    image_digest = _digest(identity[field], f"{surface} environment image")

    changed = copy.deepcopy(dict(evidence))
    changed_execution = cast(dict[str, object], changed["execution"])
    changed_execution["environment_image_digest"] = image_digest
    changed.pop("evidence_digest", None)
    changed["evidence_digest"] = sha256_bytes(canonical_bytes(changed))
    return changed


def _legacy_view(evidence: Mapping[str, object], label: str) -> dict[str, object]:
    _validate_evidence_digest(evidence, label)
    execution = evidence.get("execution")
    if not isinstance(execution, Mapping) or set(execution) != _ENRICHED_EXECUTION_FIELDS:
        actual = sorted(execution) if isinstance(execution, Mapping) else type(execution).__name__
        raise SkillsBenchAdapterError(
            f"{label} enriched execution envelope differs: "
            f"expected={sorted(_ENRICHED_EXECUTION_FIELDS)} actual={actual}"
        )
    _digest(
        execution.get("environment_image_digest"),
        f"{label} environment_image_digest",
    )
    changed = copy.deepcopy(dict(evidence))
    changed_execution = cast(dict[str, object], changed["execution"])
    changed_execution.pop("environment_image_digest")
    changed.pop("evidence_digest", None)
    changed["evidence_digest"] = sha256_bytes(canonical_bytes(changed))
    return changed


def bind_execution_parity_with_environment_image(
    report: Mapping[str, object],
    upstream_evidence: Mapping[str, object],
    normalized_evidence: Mapping[str, object],
) -> dict[str, object]:
    # Authenticate both enriched documents before comparing any of their fields.
    _validate_evidence_digest(upstream_evidence, "upstream evidence")
    _validate_evidence_digest(normalized_evidence, "normalized evidence")
    upstream_execution = upstream_evidence.get("execution")
    normalized_execution = normalized_evidence.get("execution")
    if not isinstance(upstream_execution, Mapping) or not isinstance(
        normalized_execution, Mapping
    ):
        raise SkillsBenchAdapterError("execution image evidence is absent")
    upstream_image = _digest(
        upstream_execution.get("environment_image_digest"),
        "upstream environment image",
    )
    normalized_image = _digest(
        normalized_execution.get("environment_image_digest"),
        "normalized environment image",
    )
    if upstream_image != normalized_image:
        raise SkillsBenchAdapterError(
            "execution evidence uses different environment image identities"
        )

    result = bind_execution_parity(
        report,
        _legacy_view(upstream_evidence, "upstream evidence"),
        _legacy_view(normalized_evidence, "normalized evidence"),
    )
    result_execution = result.get("execution")
    if not isinstance(result_execution, dict):
        raise SkillsBenchAdapterError("bound parity report execution result is absent")
    result_execution["upstream_evidence_digest"] = upstream_evidence["evidence_digest"]
    result_execution["normalized_evidence_digest"] = normalized_evidence[
        "evidence_digest"
    ]
    result_execution["environment_image_digest"] = upstream_image
    result_execution["same_environment_image_digest"] = True
    result["report_digest"] = report_digest(result)
    return result
