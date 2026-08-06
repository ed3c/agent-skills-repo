from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Mapping, cast

from .common import canonical_bytes, sha256_bytes
from .models import SkillsBenchAdapterError
from .task import discover_task_files

PARITY_SCHEMA_VERSION = "skillsbench-parity-report@1"
EXECUTION_EVIDENCE_SCHEMA_VERSION = "skillsbench-execution-evidence@1"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def report_digest(report: Mapping[str, object]) -> str:
    payload = {key: value for key, value in report.items() if key != "report_digest"}
    return sha256_bytes(canonical_bytes(payload))


def structural_parity_report(
    *,
    task_id: str,
    bundle_digest: str,
    upstream_package: Path,
    normalized_package: Path,
) -> dict[str, object]:
    source_files = discover_task_files(upstream_package)
    normalized_files = discover_task_files(normalized_package)
    if source_files != normalized_files:
        source_by_path = {cast(str, item["path"]): item for item in source_files}
        normalized_by_path = {cast(str, item["path"]): item for item in normalized_files}
        missing = sorted(source_by_path.keys() - normalized_by_path.keys())
        extra = sorted(normalized_by_path.keys() - source_by_path.keys())
        changed = sorted(
            path
            for path in source_by_path.keys() & normalized_by_path.keys()
            if source_by_path[path] != normalized_by_path[path]
        )
        raise SkillsBenchAdapterError(
            f"normalized package differs from upstream: missing={missing}"
            f" extra={extra} changed={changed}"
        )
    report: dict[str, object] = {
        "schema_version": PARITY_SCHEMA_VERSION,
        "task_id": task_id,
        "bundle_digest": bundle_digest,
        "status": "known_loss",
        "ranking_eligible": False,
        "structural": {
            "status": "equivalent",
            "compared_file_count": len(source_files),
            "file_set_digest": sha256_bytes(canonical_bytes({"files": source_files})),
            "task_bytes_preserved": True,
            "skill_injection_boundary_preserved": True,
        },
        "execution": {
            "status": "not_run",
            "reason": (
                "oracle and same-verifier-probe parity have not yet been executed;"
                " structural equivalence alone is not ranking evidence"
            ),
        },
        "known_losses": ["execution parity evidence is absent"],
    }
    report["report_digest"] = report_digest(report)
    return report


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise SkillsBenchAdapterError(
            f"{label} fields differ: expected={sorted(expected)}"
            f" actual={sorted(value)}"
        )


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise SkillsBenchAdapterError(f"{label} must be a sha256 digest")
    return value


def _number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SkillsBenchAdapterError(f"{label} must be numeric")
    return float(value)


def _validate_execution_evidence(
    value: Mapping[str, object],
    label: str,
    *,
    expected_surface: str,
    task_id: str,
    bundle_digest: str,
) -> tuple[Mapping[str, object], Mapping[str, object], Mapping[str, object], Mapping[str, object]]:
    _exact_keys(
        value,
        {
            "schema_version",
            "task_id",
            "bundle_digest",
            "surface",
            "upstream",
            "execution",
            "task_check_passed",
            "oracle",
            "verifier_probe",
            "evidence_digest",
        },
        label,
    )
    if value.get("schema_version") != EXECUTION_EVIDENCE_SCHEMA_VERSION:
        raise SkillsBenchAdapterError(f"{label} has unsupported schema_version")
    if value.get("task_id") != task_id:
        raise SkillsBenchAdapterError(f"{label} is bound to another task")
    if value.get("bundle_digest") != bundle_digest:
        raise SkillsBenchAdapterError(f"{label} is bound to another bundle")
    if value.get("surface") != expected_surface:
        raise SkillsBenchAdapterError(
            f"{label} surface must be {expected_surface!r}"
        )
    if value.get("task_check_passed") is not True:
        raise SkillsBenchAdapterError(f"{label} task check did not pass")

    upstream = value.get("upstream")
    execution = value.get("execution")
    oracle = value.get("oracle")
    probe = value.get("verifier_probe")
    if not all(isinstance(item, Mapping) for item in (upstream, execution, oracle, probe)):
        raise SkillsBenchAdapterError(
            f"{label} upstream, execution, oracle, and verifier_probe must be objects"
        )
    upstream = cast(Mapping[str, object], upstream)
    execution = cast(Mapping[str, object], execution)
    oracle = cast(Mapping[str, object], oracle)
    probe = cast(Mapping[str, object], probe)

    _exact_keys(upstream, {"repository", "commit"}, f"{label}.upstream")
    repository = upstream.get("repository")
    commit = upstream.get("commit")
    if not isinstance(repository, str) or "/" not in repository:
        raise SkillsBenchAdapterError(f"{label} upstream repository is invalid")
    if not isinstance(commit, str) or not FULL_SHA_RE.fullmatch(commit):
        raise SkillsBenchAdapterError(f"{label} upstream commit is not a full SHA")

    _exact_keys(
        execution,
        {"benchflow_version", "agent", "sandbox"},
        f"{label}.execution",
    )
    version = execution.get("benchflow_version")
    if not isinstance(version, str) or not version:
        raise SkillsBenchAdapterError(f"{label} BenchFlow version is invalid")
    if execution.get("agent") != "oracle":
        raise SkillsBenchAdapterError(f"{label} execution agent must be oracle")
    if execution.get("sandbox") != "docker":
        raise SkillsBenchAdapterError(f"{label} execution sandbox must be docker")

    _exact_keys(
        oracle,
        {"result_digest", "task_digest", "reward", "error", "verifier_error"},
        f"{label}.oracle",
    )
    _digest(oracle.get("result_digest"), f"{label} oracle result_digest")
    _digest(oracle.get("task_digest"), f"{label} oracle task_digest")
    _number(oracle.get("reward"), f"{label} oracle reward")
    for field in ("error", "verifier_error"):
        error = oracle.get(field)
        if error is not None and not isinstance(error, str):
            raise SkillsBenchAdapterError(f"{label} oracle {field} is invalid")

    _exact_keys(
        probe,
        {"input_digest", "reward", "diagnostics_class", "diagnostics_digest"},
        f"{label}.verifier_probe",
    )
    _digest(probe.get("input_digest"), f"{label} verifier input_digest")
    _number(probe.get("reward"), f"{label} verifier reward")
    diagnostics_class = probe.get("diagnostics_class")
    if not isinstance(diagnostics_class, str) or not diagnostics_class:
        raise SkillsBenchAdapterError(f"{label} diagnostics_class is invalid")
    _digest(probe.get("diagnostics_digest"), f"{label} diagnostics_digest")

    claimed = value.get("evidence_digest")
    expected = sha256_bytes(
        canonical_bytes({key: item for key, item in value.items() if key != "evidence_digest"})
    )
    if claimed != expected:
        raise SkillsBenchAdapterError(f"{label} evidence_digest mismatch")
    return upstream, execution, oracle, probe


def bind_execution_parity(
    report: Mapping[str, object],
    upstream_evidence: Mapping[str, object],
    normalized_evidence: Mapping[str, object],
) -> dict[str, object]:
    changed = copy.deepcopy(dict(report))
    if changed.get("schema_version") != PARITY_SCHEMA_VERSION:
        raise SkillsBenchAdapterError("parity report schema_version is unsupported")
    if changed.get("report_digest") != report_digest(changed):
        raise SkillsBenchAdapterError("parity report digest mismatch")
    task_id = changed.get("task_id")
    bundle_digest = changed.get("bundle_digest")
    if not isinstance(task_id, str) or not task_id:
        raise SkillsBenchAdapterError("parity report task_id is invalid")
    bundle_digest = _digest(bundle_digest, "parity report bundle_digest")
    structural = changed.get("structural")
    if not isinstance(structural, Mapping) or structural.get("status") != "equivalent":
        raise SkillsBenchAdapterError("execution evidence cannot rescue structural mismatch")

    upstream_identity, upstream_execution, upstream_oracle, upstream_probe = (
        _validate_execution_evidence(
            upstream_evidence,
            "upstream evidence",
            expected_surface="upstream",
            task_id=task_id,
            bundle_digest=bundle_digest,
        )
    )
    normalized_identity, normalized_execution, normalized_oracle, normalized_probe = (
        _validate_execution_evidence(
            normalized_evidence,
            "normalized evidence",
            expected_surface="normalized",
            task_id=task_id,
            bundle_digest=bundle_digest,
        )
    )
    if upstream_identity != normalized_identity:
        raise SkillsBenchAdapterError("execution evidence uses different upstream identities")
    if upstream_execution != normalized_execution:
        raise SkillsBenchAdapterError("execution evidence uses different execution envelopes")

    same_task_digest = (
        upstream_oracle["task_digest"] == normalized_oracle["task_digest"]
    )
    same_probe_input = (
        upstream_probe["input_digest"] == normalized_probe["input_digest"]
    )
    reward_one = (
        _number(upstream_oracle["reward"], "upstream oracle reward") == 1.0
        and _number(normalized_oracle["reward"], "normalized oracle reward") == 1.0
    )
    error_free = (
        upstream_oracle["error"] is None
        and normalized_oracle["error"] is None
        and upstream_oracle["verifier_error"] is None
        and normalized_oracle["verifier_error"] is None
    )
    verifier_reward_equal = upstream_probe["reward"] == normalized_probe["reward"]
    diagnostics_class_equal = (
        upstream_probe["diagnostics_class"] == normalized_probe["diagnostics_class"]
    )
    diagnostics_digest_equal = (
        upstream_probe["diagnostics_digest"] == normalized_probe["diagnostics_digest"]
    )
    equivalent = all(
        (
            same_task_digest,
            same_probe_input,
            reward_one,
            error_free,
            verifier_reward_equal,
            diagnostics_class_equal,
            diagnostics_digest_equal,
        )
    )
    changed["execution"] = {
        "status": "equivalent" if equivalent else "different",
        "upstream_evidence_digest": upstream_evidence["evidence_digest"],
        "normalized_evidence_digest": normalized_evidence["evidence_digest"],
        "upstream_repository": upstream_identity["repository"],
        "upstream_commit": upstream_identity["commit"],
        "benchflow_version": upstream_execution["benchflow_version"],
        "agent": upstream_execution["agent"],
        "sandbox": upstream_execution["sandbox"],
        "same_oracle_task_digest": same_task_digest,
        "same_probe_input": same_probe_input,
        "oracle_reward_one_on_both": reward_one,
        "oracle_error_free_on_both": error_free,
        "verifier_reward_equal": verifier_reward_equal,
        "diagnostics_class_equal": diagnostics_class_equal,
        "diagnostics_digest_equal": diagnostics_digest_equal,
    }
    changed["status"] = "equivalent" if equivalent else "rejected"
    changed["ranking_eligible"] = equivalent
    changed["known_losses"] = [] if equivalent else ["execution parity differs"]
    changed["report_digest"] = report_digest(changed)
    return changed
