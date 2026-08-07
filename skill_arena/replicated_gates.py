"""Replicated hard-gate policy for stochastic qualification cases.

The legacy ``evaluate_hard_gates`` entrypoint remains single-shot and keeps the
meaning of already-landed run documents. New qualification runs may opt into
this module's ``qualification-run@2`` contract, which requires a complete,
preregistered repetition set before any gate result can be produced.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, TypedDict, cast

POLICY_SCHEMA_VERSION = "hard-gate-repetition-policy@1"
RESULT_SCHEMA_VERSION = "replicated-hard-gate-result@1"

_ALLOWED_GROUPS = ("critical", "anchor", "target")
_POLICY_FIELDS = {
    "schema_version",
    "policy_id",
    "status",
    "effective_scope",
    "legacy_single_shot_policy_id",
    "legacy_run_handling",
    "repetitions_per_case",
    "critical_anchor_mode",
    "mixed_critical_anchor_action",
    "target_mode",
    "cost_basis",
    "cost_attempt_multiplier",
}
_ROW_FIELDS = {
    "case_id",
    "group",
    "repetition",
    "attempt_id",
    "passed",
    "evidence_digest",
}
_GATE_ORDER = (
    "critical_case_unstable",
    "anchor_case_unstable",
    "critical_failure",
    "anchor_failure",
    "target_threshold",
)


class ReplicatedGateError(ValueError):
    """The policy or run matrix is inadmissible, so no verdict may be issued."""


class CaseSummary(TypedDict):
    case_id: str
    group: Literal["critical", "anchor", "target"]
    repetitions: int
    passed_attempts: int
    failed_attempts: int
    status: Literal["all_pass", "all_fail", "mixed"]


class ReplicatedHardGateResult(TypedDict):
    schema_version: Literal["replicated-hard-gate-result@1"]
    policy_id: str
    policy_digest: str
    hard_gate_evidence_digest: str
    promotion_allowed: bool
    failed_gates: list[str]
    target_success_rate: float
    target_success_rate_ppm: int
    target_success_threshold_ppm: int
    unique_case_count: int
    metered_attempt_count: int
    repetitions_per_case: int
    cost_basis: dict[str, int | str]
    unstable_cases: list[str]
    case_summaries: list[CaseSummary]
    legacy_runs_reinterpreted: Literal[False]
    llm_judge: str | None
    llm_judge_authority: Literal["advisory_only"]


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_json(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


@dataclass(frozen=True)
class ReplicatedHardGatePolicy:
    policy_id: str
    repetitions_per_case: int
    critical_anchor_mode: Literal["all-pass-or-invalid"]
    mixed_critical_anchor_action: Literal["invalidate-run-and-retire-case"]
    target_mode: Literal["pooled-attempt-rate"]
    cost_basis: Literal["metered-attempt-count"]
    cost_attempt_multiplier: int
    status: Literal["adopted"] = "adopted"
    effective_scope: Literal["qualification-run@2"] = "qualification-run@2"
    legacy_single_shot_policy_id: Literal["hard-gate-single-shot@1"] = (
        "hard-gate-single-shot@1"
    )
    legacy_run_handling: Literal["preserve-no-rejudging"] = (
        "preserve-no-rejudging"
    )

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ReplicatedHardGatePolicy":
        if set(value) != _POLICY_FIELDS:
            raise ReplicatedGateError(
                "repetition policy fields mismatch: "
                f"expected={sorted(_POLICY_FIELDS)} actual={sorted(value)}"
            )
        exact = {
            "schema_version": POLICY_SCHEMA_VERSION,
            "status": "adopted",
            "effective_scope": "qualification-run@2",
            "legacy_single_shot_policy_id": "hard-gate-single-shot@1",
            "legacy_run_handling": "preserve-no-rejudging",
            "critical_anchor_mode": "all-pass-or-invalid",
            "mixed_critical_anchor_action": "invalidate-run-and-retire-case",
            "target_mode": "pooled-attempt-rate",
            "cost_basis": "metered-attempt-count",
        }
        for field, expected in exact.items():
            if value.get(field) != expected:
                raise ReplicatedGateError(
                    f"repetition policy {field} must equal {expected!r}"
                )
        policy_id = value.get("policy_id")
        repetitions = value.get("repetitions_per_case")
        multiplier = value.get("cost_attempt_multiplier")
        if not isinstance(policy_id, str) or not policy_id:
            raise ReplicatedGateError("repetition policy_id must be non-empty")
        if type(repetitions) is not int or not 3 <= cast(int, repetitions) <= 100:
            raise ReplicatedGateError("repetitions_per_case must be within 3..100")
        if type(multiplier) is not int or multiplier != repetitions:
            raise ReplicatedGateError(
                "cost_attempt_multiplier must equal repetitions_per_case"
            )
        return cls(
            policy_id=policy_id,
            repetitions_per_case=cast(int, repetitions),
            critical_anchor_mode="all-pass-or-invalid",
            mixed_critical_anchor_action="invalidate-run-and-retire-case",
            target_mode="pooled-attempt-rate",
            cost_basis="metered-attempt-count",
            cost_attempt_multiplier=cast(int, multiplier),
        )

    def as_mapping(self) -> dict[str, object]:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "policy_id": self.policy_id,
            "status": self.status,
            "effective_scope": self.effective_scope,
            "legacy_single_shot_policy_id": self.legacy_single_shot_policy_id,
            "legacy_run_handling": self.legacy_run_handling,
            "repetitions_per_case": self.repetitions_per_case,
            "critical_anchor_mode": self.critical_anchor_mode,
            "mixed_critical_anchor_action": self.mixed_critical_anchor_action,
            "target_mode": self.target_mode,
            "cost_basis": self.cost_basis,
            "cost_attempt_multiplier": self.cost_attempt_multiplier,
        }

    @property
    def digest(self) -> str:
        return _sha256_json(self.as_mapping())


def required_metered_attempt_count(
    policy: ReplicatedHardGatePolicy,
    unique_case_count: int,
) -> int:
    if type(unique_case_count) is not int or unique_case_count < 1:
        raise ReplicatedGateError("unique_case_count must be a positive integer")
    return unique_case_count * policy.cost_attempt_multiplier


def assert_cost_budget_attempt_count(
    result: Mapping[str, object],
    *,
    budget_judged_attempt_count: int,
) -> None:
    if type(budget_judged_attempt_count) is not int or budget_judged_attempt_count < 1:
        raise ReplicatedGateError(
            "budget_judged_attempt_count must be a positive integer"
        )
    measured = result.get("metered_attempt_count")
    if measured != budget_judged_attempt_count:
        raise ReplicatedGateError(
            "cost budget attempt basis mismatch: "
            f"gate={measured} budget={budget_judged_attempt_count}"
        )


def evaluate_replicated_hard_gates(
    cases: Iterable[Mapping[str, object]],
    *,
    policy: ReplicatedHardGatePolicy | Mapping[str, object],
    target_success_threshold_ppm: int,
    llm_judge: str | None = None,
) -> ReplicatedHardGateResult:
    """Evaluate a complete repeated-draw matrix without reinterpreting v1 runs.

    Critical and anchor cases must be all-pass. A mixed verdict is classified as
    an unstable case and invalidates the run rather than being misreported as a
    stable candidate failure. Target attempts are pooled against the frozen ppm
    threshold because every case is required to have the same repetition count.
    """
    resolved_policy = (
        policy
        if isinstance(policy, ReplicatedHardGatePolicy)
        else ReplicatedHardGatePolicy.from_mapping(policy)
    )
    if (
        type(target_success_threshold_ppm) is not int
        or not 1 <= target_success_threshold_ppm <= 1_000_000
    ):
        raise ReplicatedGateError(
            "target success threshold must be 1..1,000,000 ppm"
        )

    rows = list(cases)
    if not rows:
        raise ReplicatedGateError("replicated hard gate requires at least one row")

    attempts: set[str] = set()
    coordinates: set[tuple[str, int]] = set()
    grouped: dict[str, list[Mapping[str, object]]] = {}
    group_by_case: dict[str, str] = {}

    for index, row in enumerate(rows):
        if set(row) != _ROW_FIELDS:
            raise ReplicatedGateError(
                f"attempt row {index} has unknown or missing fields"
            )
        case_id = row.get("case_id")
        group = row.get("group")
        repetition = row.get("repetition")
        attempt_id = row.get("attempt_id")
        passed = row.get("passed")
        evidence_digest = row.get("evidence_digest")
        if not isinstance(case_id, str) or not case_id:
            raise ReplicatedGateError(f"attempt row {index} has invalid case_id")
        if group not in _ALLOWED_GROUPS:
            raise ReplicatedGateError(f"attempt row {index} has unknown group")
        if (
            type(repetition) is not int
            or not 1 <= cast(int, repetition) <= resolved_policy.repetitions_per_case
        ):
            raise ReplicatedGateError(f"attempt row {index} has invalid repetition")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ReplicatedGateError(f"attempt row {index} has invalid attempt_id")
        if type(passed) is not bool:
            raise ReplicatedGateError(f"attempt row {index} has untyped verdict")
        if not _is_sha256(evidence_digest):
            raise ReplicatedGateError(
                f"attempt row {index} has invalid evidence_digest"
            )
        if attempt_id in attempts:
            raise ReplicatedGateError("duplicate attempt_id")
        coordinate = (case_id, cast(int, repetition))
        if coordinate in coordinates:
            raise ReplicatedGateError("duplicate case/repetition coordinate")
        attempts.add(attempt_id)
        coordinates.add(coordinate)
        previous_group = group_by_case.setdefault(case_id, cast(str, group))
        if previous_group != group:
            raise ReplicatedGateError("one case_id cannot span multiple groups")
        grouped.setdefault(case_id, []).append(row)

    groups_present = set(group_by_case.values())
    missing_groups = [group for group in _ALLOWED_GROUPS if group not in groups_present]
    if missing_groups:
        raise ReplicatedGateError(
            "replicated hard gate is missing groups: " + ",".join(missing_groups)
        )

    expected_repetitions = set(range(1, resolved_policy.repetitions_per_case + 1))
    summaries: list[CaseSummary] = []
    unstable_cases: list[str] = []
    failed_set: set[str] = set()
    target_passes = 0
    target_attempts = 0

    for case_id in sorted(grouped):
        case_rows = grouped[case_id]
        repetitions = {cast(int, row["repetition"]) for row in case_rows}
        if repetitions != expected_repetitions:
            raise ReplicatedGateError(
                f"case {case_id!r} does not cover the exact repetition set"
            )
        group = cast(Literal["critical", "anchor", "target"], group_by_case[case_id])
        passed_attempts = sum(1 for row in case_rows if row["passed"] is True)
        failed_attempts = len(case_rows) - passed_attempts
        if passed_attempts == len(case_rows):
            status: Literal["all_pass", "all_fail", "mixed"] = "all_pass"
        elif passed_attempts == 0:
            status = "all_fail"
        else:
            status = "mixed"
        summaries.append(
            {
                "case_id": case_id,
                "group": group,
                "repetitions": len(case_rows),
                "passed_attempts": passed_attempts,
                "failed_attempts": failed_attempts,
                "status": status,
            }
        )
        if group in {"critical", "anchor"}:
            if status == "mixed":
                unstable_cases.append(case_id)
                failed_set.add(f"{group}_case_unstable")
            elif status == "all_fail":
                failed_set.add(f"{group}_failure")
        else:
            target_passes += passed_attempts
            target_attempts += len(case_rows)

    target_rate = target_passes / target_attempts if target_attempts else 0.0
    target_rate_ppm = (
        target_passes * 1_000_000 // target_attempts if target_attempts else 0
    )
    if target_rate_ppm < target_success_threshold_ppm:
        failed_set.add("target_threshold")
    failed_gates = [name for name in _GATE_ORDER if name in failed_set]
    unique_case_count = len(grouped)
    metered_attempt_count = required_metered_attempt_count(
        resolved_policy, unique_case_count
    )
    if metered_attempt_count != len(rows):
        raise ReplicatedGateError("metered attempt count does not match the policy")

    result_without_digest: dict[str, object] = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "policy_id": resolved_policy.policy_id,
        "policy_digest": resolved_policy.digest,
        "promotion_allowed": not failed_gates,
        "failed_gates": failed_gates,
        "target_success_rate": target_rate,
        "target_success_rate_ppm": target_rate_ppm,
        "target_success_threshold_ppm": target_success_threshold_ppm,
        "unique_case_count": unique_case_count,
        "metered_attempt_count": metered_attempt_count,
        "repetitions_per_case": resolved_policy.repetitions_per_case,
        "cost_basis": {
            "unit": resolved_policy.cost_basis,
            "attempt_count": metered_attempt_count,
            "attempt_multiplier_per_case": resolved_policy.cost_attempt_multiplier,
        },
        "unstable_cases": sorted(unstable_cases),
        "case_summaries": summaries,
        "legacy_runs_reinterpreted": False,
        "llm_judge": llm_judge,
        "llm_judge_authority": "advisory_only",
    }
    result = {
        **result_without_digest,
        "hard_gate_evidence_digest": _sha256_json(result_without_digest),
    }
    return cast(ReplicatedHardGateResult, result)
