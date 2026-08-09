"""Strict data contracts shared by the Arena experiment planner and runner."""
from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Mapping, Protocol, Sequence

SPEC_SCHEMA = "arena-experiment-spec@1"
PLAN_SCHEMA = "arena-experiment-plan@1"
SIGNED_PLAN_SCHEMA = "arena-experiment-plan-envelope@1"
INVOCATION_SCHEMA = "arena-experiment-invocation@1"
OUTCOME_SCHEMA = "arena-experiment-outcome@1"
METRICS_SCHEMA = "arena-experiment-metrics@1"
TRAJECTORY_SCHEMA = "arena-experiment-trajectory@1"
VERIFIER_SCHEMA = "arena-experiment-verifier@1"
INVOCATION_MANIFEST_SCHEMA = "arena-experiment-invocation-manifest@1"
RUN_INDEX_SCHEMA = "arena-experiment-run-index@1"
BUNDLE_MANIFEST_SCHEMA = "arena-experiment-bundle-manifest@1"
SIGNED_BUNDLE_SCHEMA = "arena-experiment-bundle-envelope@1"

PLAN_SIGNATURE_DOMAIN = b"agent-skills-repo:arena-experiment-plan:v1\x00"
BUNDLE_SIGNATURE_DOMAIN = b"agent-skills-repo:arena-experiment-bundle:v1\x00"
RANDOMIZATION_ALGORITHM = "sha256-arm-sort@1"
FAILURE_DENOMINATOR_POLICY = "all-preregistered-invocations-count"
WORKSPACE_POLICY = "fresh-ephemeral-per-invocation"
NO_SKILL = "no-skill"

OUTCOME_CLASSES = frozenset(
    {
        "succeeded",
        "task_failure",
        "verifier_failure",
        "agent_refusal",
        "timeout",
        "transport_loss",
        "infrastructure_failure",
    }
)
NETWORK_POLICIES = frozenset({"no-network", "allowlisted", "public"})
AGENT_SEED_MODES = frozenset({"deterministic", "unavailable"})
REQUIRED_EVIDENCE_FILES = (
    "invocation.json",
    "metrics.json",
    "outcome.json",
    "stderr.bin",
    "stdout.bin",
    "trajectory.json",
    "verifier.json",
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,191}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RELATIVE_PATH_RE = re.compile(r"^[^\\\x00]+$")

_SPEC_FIELDS = {
    "schema_version",
    "experiment_id",
    "tasks",
    "candidate_skill_artifact_digest",
    "placebo_skill_artifact_digest",
    "agent_id",
    "model_id",
    "harness_id",
    "harness_version",
    "sandbox_profile_id",
    "environment_image_digest",
    "policy_digest",
    "network_policy",
    "allowed_tools_digest",
    "repetitions",
    "randomization_seed",
    "agent_seed_mode",
    "preregistered_at",
}
_TASK_FIELDS = {"task_id", "task_family", "task_bundle_digest"}
_PLAN_FIELDS = {
    "schema_version",
    "randomization_algorithm",
    "failure_denominator_policy",
    "workspace_policy",
    "spec",
    "arms",
    "blocks",
    "invocations",
    "planned_invocation_count",
    "required_evidence_files",
    "plan_digest",
}
_PLAN_ENVELOPE_FIELDS = {
    "schema_version",
    "payload",
    "plan_hash",
    "issuer_key_id",
    "signature",
}
_METRICS_FIELDS = {
    "schema_version",
    "end_to_end_latency_ms",
    "verifier_latency_ms",
    "input_tokens",
    "output_tokens",
    "tool_tokens",
    "cost_microunits",
    "cpu_time_ms",
    "peak_memory_bytes",
    "tool_call_count",
}


class ExperimentError(ValueError):
    """Experiment evidence is malformed, incomplete, or not admissible."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_json(value: object) -> str:
    return sha256_bytes(canonical_bytes(value))


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def require_sha256(value: object, label: str) -> str:
    if not is_sha256(value):
        raise ExperimentError(f"{label} must be a sha256 digest")
    return str(value)


def identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise ExperimentError(f"{label} is not a valid identifier")
    return value


def timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExperimentError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExperimentError(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExperimentError(f"{label} must include a timezone")
    return value


def validate_relative_artifact_path(value: object) -> str:
    if not isinstance(value, str) or not value or _RELATIVE_PATH_RE.fullmatch(value) is None:
        raise ExperimentError("output artifact path must be a non-empty relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ExperimentError(f"output artifact path is unsafe: {value!r}")
    if value.startswith("artifacts/"):
        raise ExperimentError("adapter artifact paths are relative to artifacts/, not prefixed")
    return value


def regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ExperimentError(f"{label} must be a regular file: {path}")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ExperimentError(f"{label} is not a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise ExperimentError(f"cannot read {label}: {path}: {exc}") from exc


def validate_spec(spec: Mapping[str, object]) -> dict[str, object]:
    if set(spec) != _SPEC_FIELDS or spec.get("schema_version") != SPEC_SCHEMA:
        raise ExperimentError("experiment spec schema or fields are invalid")

    normalized: dict[str, object] = {
        "schema_version": SPEC_SCHEMA,
        "experiment_id": identifier(spec.get("experiment_id"), "experiment_id"),
    }
    raw_tasks = spec.get("tasks")
    if not isinstance(raw_tasks, Sequence) or isinstance(raw_tasks, (str, bytes)):
        raise ExperimentError("experiment tasks must be a non-empty list")
    if not raw_tasks:
        raise ExperimentError("experiment tasks must be a non-empty list")
    tasks: list[dict[str, str]] = []
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, Mapping) or set(raw_task) != _TASK_FIELDS:
            raise ExperimentError(f"experiment task {index} fields are invalid")
        tasks.append(
            {
                "task_id": identifier(raw_task.get("task_id"), f"task {index} task_id"),
                "task_family": identifier(
                    raw_task.get("task_family"), f"task {index} task_family"
                ),
                "task_bundle_digest": require_sha256(
                    raw_task.get("task_bundle_digest"),
                    f"task {index} task_bundle_digest",
                ),
            }
        )
    if tasks != sorted(tasks, key=lambda item: item["task_id"]):
        raise ExperimentError("experiment tasks must be sorted by task_id")
    task_ids = [item["task_id"] for item in tasks]
    task_digests = [item["task_bundle_digest"] for item in tasks]
    if len(task_ids) != len(set(task_ids)) or len(task_digests) != len(set(task_digests)):
        raise ExperimentError("experiment tasks contain duplicate ids or bundle digests")
    normalized["tasks"] = tasks

    candidate = require_sha256(
        spec.get("candidate_skill_artifact_digest"),
        "candidate_skill_artifact_digest",
    )
    placebo_raw = spec.get("placebo_skill_artifact_digest")
    placebo = (
        None
        if placebo_raw is None
        else require_sha256(placebo_raw, "placebo_skill_artifact_digest")
    )
    if placebo == candidate:
        raise ExperimentError("candidate and placebo skill digests must differ")
    normalized["candidate_skill_artifact_digest"] = candidate
    normalized["placebo_skill_artifact_digest"] = placebo

    for field in (
        "agent_id",
        "model_id",
        "harness_id",
        "harness_version",
        "sandbox_profile_id",
    ):
        normalized[field] = identifier(spec.get(field), field)
    for field in (
        "environment_image_digest",
        "policy_digest",
        "allowed_tools_digest",
    ):
        normalized[field] = require_sha256(spec.get(field), field)

    network_policy = spec.get("network_policy")
    if network_policy not in NETWORK_POLICIES:
        raise ExperimentError("network_policy is unsupported")
    normalized["network_policy"] = network_policy

    repetitions = spec.get("repetitions")
    if type(repetitions) is not int or not 3 <= repetitions <= 100:
        raise ExperimentError("repetitions must be an integer in 3..100")
    normalized["repetitions"] = repetitions

    randomization_seed = spec.get("randomization_seed")
    if (
        type(randomization_seed) is not int
        or not 0 <= randomization_seed <= (1 << 63) - 1
    ):
        raise ExperimentError("randomization_seed must be an unsigned 63-bit integer")
    normalized["randomization_seed"] = randomization_seed

    agent_seed_mode = spec.get("agent_seed_mode")
    if agent_seed_mode not in AGENT_SEED_MODES:
        raise ExperimentError("agent_seed_mode is unsupported")
    normalized["agent_seed_mode"] = agent_seed_mode
    normalized["preregistered_at"] = timestamp(
        spec.get("preregistered_at"), "preregistered_at"
    )
    return normalized


@dataclass(frozen=True)
class InvocationCapture:
    classification: str
    reward: float | None
    adapter_exit_code: int | None
    error_code: str | None
    stdout: bytes
    stderr: bytes
    trajectory: Mapping[str, object]
    verifier: Mapping[str, object]
    metrics: Mapping[str, object]
    artifacts: Mapping[str, bytes]
    started_at: datetime
    completed_at: datetime


class ExperimentAdapter(Protocol):
    def execute(
        self,
        invocation: Mapping[str, object],
        workspace: Path,
    ) -> InvocationCapture:
        """Execute exactly one invocation inside the supplied fresh workspace."""


__all__ = [
    "AGENT_SEED_MODES",
    "BUNDLE_MANIFEST_SCHEMA",
    "BUNDLE_SIGNATURE_DOMAIN",
    "ExperimentAdapter",
    "ExperimentError",
    "FAILURE_DENOMINATOR_POLICY",
    "INVOCATION_MANIFEST_SCHEMA",
    "INVOCATION_SCHEMA",
    "InvocationCapture",
    "METRICS_SCHEMA",
    "NETWORK_POLICIES",
    "NO_SKILL",
    "OUTCOME_CLASSES",
    "OUTCOME_SCHEMA",
    "PLAN_SCHEMA",
    "PLAN_SIGNATURE_DOMAIN",
    "RANDOMIZATION_ALGORITHM",
    "REQUIRED_EVIDENCE_FILES",
    "RUN_INDEX_SCHEMA",
    "SIGNED_BUNDLE_SCHEMA",
    "SIGNED_PLAN_SCHEMA",
    "SPEC_SCHEMA",
    "TRAJECTORY_SCHEMA",
    "VERIFIER_SCHEMA",
    "WORKSPACE_POLICY",
    "canonical_bytes",
    "identifier",
    "is_sha256",
    "regular_file",
    "require_sha256",
    "sha256_bytes",
    "sha256_json",
    "timestamp",
    "validate_relative_artifact_path",
    "validate_spec",
]
