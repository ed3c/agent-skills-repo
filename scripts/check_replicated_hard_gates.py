#!/usr/bin/env python3
"""Evaluate one preregistered repeated-draw hard-gate matrix."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.replicated_gates import (  # noqa: E402
    ReplicatedGateError,
    ReplicatedHardGatePolicy,
    assert_cost_budget_attempt_count,
    evaluate_replicated_hard_gates,
)

DEFAULT_POLICY = ROOT / "data/qualification/hard-gate-repetition-policy-v1.json"


def _load_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplicatedGateError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReplicatedGateError(f"{label} root must be an object")
    return value


def _load_rows(path: Path) -> list[dict[str, object]]:
    document = _load_object(path, "attempt rows")
    if set(document) != {"schema_version", "attempts"}:
        raise ReplicatedGateError("attempt document has unknown or missing fields")
    if document.get("schema_version") != "replicated-hard-gate-attempts@1":
        raise ReplicatedGateError("attempt document schema_version is invalid")
    attempts = document.get("attempts")
    if not isinstance(attempts, list) or not all(
        isinstance(item, dict) for item in attempts
    ):
        raise ReplicatedGateError("attempts must be a list of objects")
    return attempts


def _selftest() -> int:
    policy = ReplicatedHardGatePolicy.from_mapping(
        _load_object(DEFAULT_POLICY, "default policy")
    )
    rows: list[dict[str, object]] = []
    for case_id, group in (
        ("critical-1", "critical"),
        ("anchor-1", "anchor"),
        ("target-1", "target"),
    ):
        for repetition in range(1, policy.repetitions_per_case + 1):
            rows.append(
                {
                    "case_id": case_id,
                    "group": group,
                    "repetition": repetition,
                    "attempt_id": f"{case_id}-{repetition}",
                    "passed": True,
                    "evidence_digest": "sha256:" + f"{len(rows) + 1:064x}",
                }
            )
    positive = evaluate_replicated_hard_gates(
        rows,
        policy=policy,
        target_success_threshold_ppm=900_000,
    )
    assert_cost_budget_attempt_count(
        positive,
        budget_judged_attempt_count=len(rows),
    )
    if not positive["promotion_allowed"]:
        raise ReplicatedGateError("positive selftest did not pass")

    rows[0]["passed"] = False
    negative = evaluate_replicated_hard_gates(
        rows,
        policy=policy,
        target_success_threshold_ppm=900_000,
    )
    if "critical_case_unstable" not in negative["failed_gates"]:
        raise ReplicatedGateError("mixed critical selftest did not fail as unstable")
    print("PASS: replicated hard-gate positive and instability controls")
    return 0


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    value.add_argument("--rows", type=Path)
    value.add_argument("--target-success-threshold-ppm", type=int)
    value.add_argument("--budget-judged-attempt-count", type=int)
    value.add_argument("--llm-judge")
    value.add_argument("--output", type=Path)
    value.add_argument("--selftest", action="store_true")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.selftest:
            if any(
                value is not None
                for value in (
                    args.rows,
                    args.target_success_threshold_ppm,
                    args.budget_judged_attempt_count,
                    args.output,
                )
            ):
                raise ReplicatedGateError("--selftest cannot be combined with run inputs")
            return _selftest()
        missing = [
            name
            for name, value in (
                ("--rows", args.rows),
                ("--target-success-threshold-ppm", args.target_success_threshold_ppm),
                ("--budget-judged-attempt-count", args.budget_judged_attempt_count),
                ("--output", args.output),
            )
            if value is None
        ]
        if missing:
            raise ReplicatedGateError("missing required run inputs: " + ", ".join(missing))
        policy = ReplicatedHardGatePolicy.from_mapping(
            _load_object(args.policy, "repetition policy")
        )
        result = evaluate_replicated_hard_gates(
            _load_rows(args.rows),
            policy=policy,
            target_success_threshold_ppm=args.target_success_threshold_ppm,
            llm_judge=args.llm_judge,
        )
        assert_cost_budget_attempt_count(
            result,
            budget_judged_attempt_count=args.budget_judged_attempt_count,
        )
        output = args.output
        if output.exists():
            raise ReplicatedGateError(f"output already exists: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except ReplicatedGateError as exc:
        print(f"FAIL: replicated hard gate: {exc}", file=sys.stderr)
        return 2
    print(
        "PASS" if result["promotion_allowed"] else "REFUSED",
        f"policy={result['policy_digest']}",
        f"attempts={result['metered_attempt_count']}",
        f"failed={','.join(result['failed_gates']) or 'none'}",
    )
    return 0 if result["promotion_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
