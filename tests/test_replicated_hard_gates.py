from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from skill_arena.replicated_gates import (
    ReplicatedGateError,
    ReplicatedHardGatePolicy,
    assert_cost_budget_attempt_count,
    evaluate_replicated_hard_gates,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data/qualification/hard-gate-repetition-policy-v1.json"
POLICY_SCHEMA_PATH = ROOT / "contracts/hard-gate-repetition-policy.schema.json"
RESULT_SCHEMA_PATH = ROOT / "contracts/replicated-hard-gate-result.schema.json"
CLI = ROOT / "scripts/check_replicated_hard_gates.py"


def policy_document() -> dict[str, object]:
    return json.loads(POLICY_PATH.read_text(encoding="utf-8"))


def policy() -> ReplicatedHardGatePolicy:
    return ReplicatedHardGatePolicy.from_mapping(policy_document())


def rows(*, target_passes: int = 5) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    p = policy()
    for case_id, group in (
        ("critical-1", "critical"),
        ("anchor-1", "anchor"),
        ("target-1", "target"),
    ):
        for repetition in range(1, p.repetitions_per_case + 1):
            passed = group != "target" or repetition <= target_passes
            result.append(
                {
                    "case_id": case_id,
                    "group": group,
                    "repetition": repetition,
                    "attempt_id": f"{case_id}-{repetition}",
                    "passed": passed,
                    "evidence_digest": "sha256:" + f"{len(result) + 1:064x}",
                }
            )
    return result


def evaluate(value: list[dict[str, object]], threshold: int = 800_000):
    return evaluate_replicated_hard_gates(
        value,
        policy=policy(),
        target_success_threshold_ppm=threshold,
        llm_judge="advisory observation",
    )


def test_policy_and_result_validate_against_schemas() -> None:
    policy_schema = json.loads(POLICY_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(policy_schema).iter_errors(policy_document())) == []

    result = evaluate(rows())
    result_schema = json.loads(RESULT_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(result_schema).iter_errors(result)) == []


def test_all_pass_matrix_is_eligible_and_prices_every_attempt() -> None:
    result = evaluate(rows())
    assert result["promotion_allowed"] is True
    assert result["failed_gates"] == []
    assert result["unique_case_count"] == 3
    assert result["metered_attempt_count"] == 15
    assert result["cost_basis"] == {
        "unit": "metered-attempt-count",
        "attempt_count": 15,
        "attempt_multiplier_per_case": 5,
    }
    assert result["target_success_rate_ppm"] == 1_000_000
    assert result["legacy_runs_reinterpreted"] is False
    assert result["llm_judge_authority"] == "advisory_only"
    assert_cost_budget_attempt_count(result, budget_judged_attempt_count=15)


def test_mixed_critical_case_is_unstable_not_a_stable_candidate_failure() -> None:
    value = rows()
    value[0]["passed"] = False
    result = evaluate(value)
    assert result["promotion_allowed"] is False
    assert result["failed_gates"] == ["critical_case_unstable"]
    assert result["unstable_cases"] == ["critical-1"]
    summary = next(item for item in result["case_summaries"] if item["case_id"] == "critical-1")
    assert summary["status"] == "mixed"


def test_all_failed_critical_case_is_a_candidate_failure() -> None:
    value = rows()
    for item in value:
        if item["case_id"] == "critical-1":
            item["passed"] = False
    result = evaluate(value)
    assert result["failed_gates"] == ["critical_failure"]
    assert result["unstable_cases"] == []


def test_mixed_anchor_case_is_invalid() -> None:
    value = rows()
    next(item for item in value if item["case_id"] == "anchor-1")["passed"] = False
    result = evaluate(value)
    assert result["failed_gates"] == ["anchor_case_unstable"]


def test_target_uses_pooled_attempt_rate() -> None:
    exact = evaluate(rows(target_passes=4), threshold=800_000)
    assert exact["promotion_allowed"] is True
    assert exact["target_success_rate_ppm"] == 800_000

    below = evaluate(rows(target_passes=3), threshold=800_000)
    assert below["promotion_allowed"] is False
    assert below["failed_gates"] == ["target_threshold"]
    assert below["target_success_rate_ppm"] == 600_000


def test_missing_repetition_refuses_to_issue_any_result() -> None:
    value = rows()
    value.pop()
    with pytest.raises(ReplicatedGateError, match="exact repetition set"):
        evaluate(value)


def test_duplicate_attempt_id_is_rejected() -> None:
    value = rows()
    value[1]["attempt_id"] = value[0]["attempt_id"]
    with pytest.raises(ReplicatedGateError, match="duplicate attempt_id"):
        evaluate(value)


def test_duplicate_case_repetition_coordinate_is_rejected() -> None:
    value = rows()
    value[1]["repetition"] = value[0]["repetition"]
    with pytest.raises(ReplicatedGateError, match="duplicate case/repetition"):
        evaluate(value)


def test_case_cannot_drift_between_groups() -> None:
    value = rows()
    value[1]["group"] = "anchor"
    with pytest.raises(ReplicatedGateError, match="multiple groups"):
        evaluate(value)


def test_unknown_attempt_field_is_rejected() -> None:
    value = rows()
    value[0]["surprise"] = True
    with pytest.raises(ReplicatedGateError, match="unknown or missing fields"):
        evaluate(value)


def test_policy_cost_multiplier_must_equal_repetitions() -> None:
    value = policy_document()
    value["cost_attempt_multiplier"] = 4
    with pytest.raises(ReplicatedGateError, match="must equal"):
        ReplicatedHardGatePolicy.from_mapping(value)


def test_cost_budget_attempt_mismatch_is_rejected() -> None:
    result = evaluate(rows())
    with pytest.raises(ReplicatedGateError, match="basis mismatch"):
        assert_cost_budget_attempt_count(result, budget_judged_attempt_count=3)


def test_policy_digest_changes_when_repetition_count_changes() -> None:
    original = policy()
    changed_document = deepcopy(policy_document())
    changed_document["repetitions_per_case"] = 6
    changed_document["cost_attempt_multiplier"] = 6
    changed = ReplicatedHardGatePolicy.from_mapping(changed_document)
    assert changed.digest != original.digest


def test_cli_selftest() -> None:
    completed = subprocess.run(
        [sys.executable, str(CLI), "--selftest"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "instability controls" in completed.stdout


def test_cli_writes_refusal_and_uses_distinct_exit_code(tmp_path: Path) -> None:
    value = rows()
    value[0]["passed"] = False
    input_path = tmp_path / "attempts.json"
    output_path = tmp_path / "result.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "replicated-hard-gate-attempts@1",
                "attempts": value,
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--rows",
            str(input_path),
            "--target-success-threshold-ppm",
            "800000",
            "--budget-judged-attempt-count",
            "15",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["failed_gates"] == ["critical_case_unstable"]


def test_cli_refuses_wrong_cost_basis_before_writing(tmp_path: Path) -> None:
    input_path = tmp_path / "attempts.json"
    output_path = tmp_path / "result.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "replicated-hard-gate-attempts@1",
                "attempts": rows(),
            }
        ),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--rows",
            str(input_path),
            "--target-success-threshold-ppm",
            "800000",
            "--budget-judged-attempt-count",
            "3",
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "basis mismatch" in completed.stderr
    assert not output_path.exists()
