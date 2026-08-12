"""Immutable study inputs for the quote-repair efficacy experiment."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Mapping, Sequence

from anchor_oracle import nearest_source_span

from .model import ExperimentError, require_sha256, sha256_json, timestamp


TASK_BUNDLE_SCHEMA = "quote-repair-task-bundle@1"
STUDY_PROTOCOL_SCHEMA = "quote-repair-study-protocol@1"
_TASK_FIELDS = {
    "task_id",
    "task_family",
    "source_path",
    "source_text",
    "planted_quote",
    "wiki_template",
    "expected_repair_quote",
    "expected_round0_status",
    "expected_diagnostic_status",
    "task_digest",
}
_BUNDLE_FIELDS = {
    "schema_version",
    "bundle_id",
    "tasks",
    "bundle_digest",
}
_PROTOCOL_FIELDS = {
    "schema_version",
    "study_id",
    "coordination_issue_url",
    "baseline_artifact",
    "candidate_artifact",
    "provider_policy_digest",
    "task_bundle_digest",
    "environment_image_digest",
    "environment_receipt_digest",
    "diagnostic_evidence_contract",
    "allowed_tools",
    "allowed_tools_digest",
    "repetitions",
    "max_repair_attempts",
    "eligibility_rule",
    "failure_denominator_policy",
    "failure_classes",
    "primary_endpoint",
    "secondary_endpoints",
    "analysis",
    "ranking_claim_allowed",
    "qualification_claim_allowed",
    "experiment_execution_authorized",
    "preregistered_at",
    "protocol_digest",
}


def _load(path: Path | str, label: str) -> dict[str, object]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ExperimentError(f"{label} is absent or unsafe")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise ExperimentError(f"{label} root must be an object")
    return value


def validate_quote_repair_task_bundle(
    bundle: Mapping[str, object],
) -> dict[str, object]:
    if set(bundle) != _BUNDLE_FIELDS or bundle.get("schema_version") != TASK_BUNDLE_SCHEMA:
        raise ExperimentError("quote-repair task bundle fields are invalid")
    if bundle.get("bundle_id") != "quote-repair-interior-elision@1":
        raise ExperimentError("quote-repair task bundle id is unsupported")
    tasks = bundle.get("tasks")
    if not isinstance(tasks, Sequence) or isinstance(tasks, (str, bytes)) or not tasks:
        raise ExperimentError("quote-repair tasks must be a non-empty list")
    normalized: list[dict[str, object]] = []
    for index, raw in enumerate(tasks):
        if not isinstance(raw, Mapping) or set(raw) != _TASK_FIELDS:
            raise ExperimentError(f"quote-repair task {index} fields are invalid")
        task = copy.deepcopy(dict(raw))
        if task.get("task_family") != "quote-repair-interior-elision":
            raise ExperimentError("quote-repair task family is unsupported")
        for field in (
            "task_id",
            "source_path",
            "source_text",
            "planted_quote",
            "wiki_template",
            "expected_repair_quote",
        ):
            if not isinstance(task.get(field), str) or not task[field]:
                raise ExperimentError(f"quote-repair task {field} is invalid")
        if task.get("expected_round0_status") != "quote_not_found":
            raise ExperimentError("quote-repair round-0 status must be quote_not_found")
        if task.get("expected_diagnostic_status") != "interior_elision":
            raise ExperimentError("quote-repair diagnostic must be interior_elision")
        source = str(task["source_text"]).encode("utf-8")
        planted = str(task["planted_quote"])
        if planted.encode("utf-8") in source:
            raise ExperimentError("quote-repair planted quote already resolves")
        diagnostic = nearest_source_span(planted, source)
        if diagnostic["status"] != "interior_elision":
            raise ExperimentError("quote-repair task is not an interior elision")
        span = diagnostic.get("span")
        if not isinstance(span, Mapping) or span.get("text") != task.get(
            "expected_repair_quote"
        ):
            raise ExperimentError("quote-repair expected span differs from oracle")
        planted_anchor = f"(src: {task['source_path']} `{planted}`)"
        if str(task["wiki_template"]).count(planted_anchor) != 1:
            raise ExperimentError("quote-repair wiki template has no unique planted anchor")
        stored = require_sha256(task.get("task_digest"), "quote-repair task digest")
        without_digest = {
            key: value for key, value in task.items() if key != "task_digest"
        }
        if stored != sha256_json(without_digest):
            raise ExperimentError("quote-repair task digest mismatch")
        normalized.append(task)
    if [task["task_id"] for task in normalized] != sorted(
        str(task["task_id"]) for task in normalized
    ):
        raise ExperimentError("quote-repair tasks must be sorted")
    stored_bundle = require_sha256(
        bundle.get("bundle_digest"), "quote-repair bundle digest"
    )
    without_bundle = {
        key: value for key, value in bundle.items() if key != "bundle_digest"
    }
    if stored_bundle != sha256_json(without_bundle):
        raise ExperimentError("quote-repair bundle digest mismatch")
    return copy.deepcopy(dict(bundle))


def load_quote_repair_task_bundle(path: Path | str) -> dict[str, object]:
    return validate_quote_repair_task_bundle(_load(path, "quote-repair task bundle"))


def validate_quote_repair_protocol(
    protocol: Mapping[str, object],
    bundle: Mapping[str, object],
) -> dict[str, object]:
    if set(protocol) != _PROTOCOL_FIELDS or protocol.get(
        "schema_version"
    ) != STUDY_PROTOCOL_SCHEMA:
        raise ExperimentError("quote-repair protocol fields are invalid")
    expected = {
        "study_id": "quote-repair-diagnostic-efficacy@1",
        "coordination_issue_url": "https://github.com/ed3c/agent-skills-repo/issues/53",
        "provider_policy_digest": "sha256:23eeb227509e73d11f38bc4796851238a8638bb3b93a128f33a34a8d3e8ac3ed",
        "task_bundle_digest": bundle.get("bundle_digest"),
        "repetitions": 5,
        "max_repair_attempts": 1,
        "eligibility_rule": "round-0-quote-not-found-and-interior-elision",
        "failure_denominator_policy": "all-preregistered-invocations-count",
        "ranking_claim_allowed": False,
        "qualification_claim_allowed": False,
        "experiment_execution_authorized": False,
    }
    for field, value in expected.items():
        if protocol.get(field) != value or type(protocol.get(field)) is not type(value):
            raise ExperimentError(f"quote-repair protocol {field} mismatch")
    for artifact_field in ("baseline_artifact", "candidate_artifact"):
        artifact = protocol.get(artifact_field)
        if not isinstance(artifact, Mapping) or set(artifact) != {
            "commit",
            "source_artifact_digest",
            "portable_artifact_digest",
        }:
            raise ExperimentError(f"quote-repair {artifact_field} is invalid")
        if not isinstance(artifact.get("commit"), str) or len(str(artifact["commit"])) != 40:
            raise ExperimentError(f"quote-repair {artifact_field} commit is invalid")
        require_sha256(artifact.get("source_artifact_digest"), artifact_field)
        require_sha256(artifact.get("portable_artifact_digest"), artifact_field)
    for field in (
        "environment_image_digest",
        "environment_receipt_digest",
        "allowed_tools_digest",
        "protocol_digest",
    ):
        require_sha256(protocol.get(field), f"quote-repair protocol {field}")
    if protocol.get("allowed_tools") != ["edit", "read", "write"]:
        raise ExperimentError("quote-repair allowed tools are unsupported")
    if protocol.get("allowed_tools_digest") != sha256_json(protocol["allowed_tools"]):
        raise ExperimentError("quote-repair allowed tools digest mismatch")
    if protocol.get("failure_classes") != [
        "succeeded",
        "task_failure",
        "verifier_failure",
        "agent_refusal",
        "timeout",
        "transport_loss",
        "infrastructure_failure",
        "malformed_repair",
        "wrong_file_or_span",
        "no_candidate",
        "search_incomplete",
    ]:
        raise ExperimentError("quote-repair failure classes are unsupported")
    if protocol.get("diagnostic_evidence_contract") != {
        "adapter_diagnostic_path": "/app/adapter-diagnostic.json",
        "schema_version": "quote-repair-adapter-diagnostic@1",
        "typed_statuses": ["no_candidate", "search_incomplete"],
        "verifier_diagnostic_path": "/logs/verifier/diagnostics.json",
        "invalid_diagnostic_status": "malformed_repair",
    }:
        raise ExperimentError("quote-repair diagnostic evidence contract is unsupported")
    if protocol.get("primary_endpoint") != {
        "name": "eligible-repaired-to-lexical-pass-within-one-attempt",
        "unit": "binary-per-invocation",
    }:
        raise ExperimentError("quote-repair primary endpoint is unsupported")
    if protocol.get("secondary_endpoints") != [
        "repair_latency_ms",
        "input_tokens",
        "output_tokens",
        "cost_microunits",
        "malformed_output_rate",
        "wrong_file_or_span_rate",
        "no_candidate_rate",
        "search_incomplete_rate",
    ]:
        raise ExperimentError("quote-repair secondary endpoints are unsupported")
    if protocol.get("analysis") != {
        "effect_estimator": "paired-risk-difference-ppm",
        "success_threshold_ppm": 200000,
        "uncertainty_method": "cluster-bootstrap-task-repetition-95pct",
        "bootstrap_resamples": 10000,
        "bootstrap_seed": 530046,
        "decision_rule": "point-estimate-gte-threshold-and-ci-lower-bound-gt-zero",
    }:
        raise ExperimentError("quote-repair analysis plan is unsupported")
    timestamp(protocol.get("preregistered_at"), "quote-repair preregistered_at")
    stored = str(protocol["protocol_digest"])
    without_digest = {
        key: value for key, value in protocol.items() if key != "protocol_digest"
    }
    if stored != sha256_json(without_digest):
        raise ExperimentError("quote-repair protocol digest mismatch")
    return copy.deepcopy(dict(protocol))


def load_quote_repair_protocol(
    path: Path | str,
    bundle: Mapping[str, object],
) -> dict[str, object]:
    return validate_quote_repair_protocol(
        _load(path, "quote-repair study protocol"), bundle
    )


__all__ = [
    "load_quote_repair_protocol",
    "load_quote_repair_task_bundle",
    "validate_quote_repair_protocol",
    "validate_quote_repair_task_bundle",
]
