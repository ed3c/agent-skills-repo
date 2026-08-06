from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, cast

from .models import SkillsBenchAdapterError
from .common import canonical_bytes, sha256_bytes
from .task import discover_task_files

PARITY_SCHEMA_VERSION = "skillsbench-parity-report@1"
EXECUTION_EVIDENCE_SCHEMA_VERSION = "skillsbench-execution-evidence@1"


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


def _validate_execution_evidence(value: Mapping[str, object], label: str) -> None:
    allowed = {
        "schema_version",
        "task_check_passed",
        "oracle_reward",
        "verifier_probe",
        "evidence_digest",
    }
    extra = sorted(set(value) - allowed)
    if extra:
        raise SkillsBenchAdapterError(f"{label} has unsupported fields: {extra}")
    if value.get("schema_version") != EXECUTION_EVIDENCE_SCHEMA_VERSION:
        raise SkillsBenchAdapterError(f"{label} has unsupported schema_version")
    if value.get("task_check_passed") is not True:
        raise SkillsBenchAdapterError(f"{label} task check did not pass")
    reward = value.get("oracle_reward")
    if not isinstance(reward, (int, float)) or isinstance(reward, bool):
        raise SkillsBenchAdapterError(f"{label} oracle_reward must be numeric")
    probe = value.get("verifier_probe")
    if not isinstance(probe, Mapping):
        raise SkillsBenchAdapterError(f"{label} verifier_probe must be an object")
    probe_allowed = {"input_digest", "reward", "diagnostics_class"}
    if set(probe) != probe_allowed:
        raise SkillsBenchAdapterError(
            f"{label} verifier_probe fields must be {sorted(probe_allowed)}"
        )
    input_digest = probe.get("input_digest")
    if not isinstance(input_digest, str) or not input_digest.startswith("sha256:"):
        raise SkillsBenchAdapterError(f"{label} verifier probe digest is invalid")
    probe_reward = probe.get("reward")
    if not isinstance(probe_reward, (int, float)) or isinstance(probe_reward, bool):
        raise SkillsBenchAdapterError(f"{label} verifier probe reward must be numeric")
    diagnostics = probe.get("diagnostics_class")
    if not isinstance(diagnostics, str) or not diagnostics:
        raise SkillsBenchAdapterError(f"{label} diagnostics_class is invalid")
    claimed = value.get("evidence_digest")
    expected = sha256_bytes(
        canonical_bytes({key: item for key, item in value.items() if key != "evidence_digest"})
    )
    if claimed != expected:
        raise SkillsBenchAdapterError(f"{label} evidence_digest mismatch")


def bind_execution_parity(
    report: Mapping[str, object],
    upstream_evidence: Mapping[str, object],
    normalized_evidence: Mapping[str, object],
) -> dict[str, object]:
    _validate_execution_evidence(upstream_evidence, "upstream evidence")
    _validate_execution_evidence(normalized_evidence, "normalized evidence")
    changed = copy.deepcopy(dict(report))
    structural = changed.get("structural")
    if not isinstance(structural, Mapping) or structural.get("status") != "equivalent":
        raise SkillsBenchAdapterError("execution evidence cannot rescue structural mismatch")

    upstream_probe = cast(Mapping[str, object], upstream_evidence["verifier_probe"])
    normalized_probe = cast(Mapping[str, object], normalized_evidence["verifier_probe"])
    equivalent = (
        float(cast(int | float, upstream_evidence["oracle_reward"])) == 1.0
        and float(cast(int | float, normalized_evidence["oracle_reward"])) == 1.0
        and upstream_probe["input_digest"] == normalized_probe["input_digest"]
        and upstream_probe["reward"] == normalized_probe["reward"]
        and upstream_probe["diagnostics_class"] == normalized_probe["diagnostics_class"]
    )
    changed["execution"] = {
        "status": "equivalent" if equivalent else "different",
        "upstream_evidence_digest": upstream_evidence["evidence_digest"],
        "normalized_evidence_digest": normalized_evidence["evidence_digest"],
        "same_probe_input": (
            upstream_probe["input_digest"] == normalized_probe["input_digest"]
        ),
        "oracle_reward_one_on_both": (
            float(cast(int | float, upstream_evidence["oracle_reward"])) == 1.0
            and float(cast(int | float, normalized_evidence["oracle_reward"])) == 1.0
        ),
        "verifier_reward_equal": upstream_probe["reward"] == normalized_probe["reward"],
        "diagnostics_class_equal": (
            upstream_probe["diagnostics_class"] == normalized_probe["diagnostics_class"]
        ),
    }
    changed["status"] = "equivalent" if equivalent else "rejected"
    changed["ranking_eligible"] = equivalent
    changed["known_losses"] = [] if equivalent else ["execution parity differs"]
    changed["report_digest"] = report_digest(changed)
    return changed
