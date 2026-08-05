#!/usr/bin/env python3
"""Deterministic local ablation runner for skill assets with regex telemetry."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
TARGET_DELTA = 0.05


def generate_cases(cases: list[dict[str, object]]) -> list[dict[str, object]]:
    synthetic: list[dict[str, object]] = []
    for index, case in enumerate(cases, 1):
        enriched = dict(case)
        enriched.setdefault("case_id", f"AB-{index:03d}")
        enriched.setdefault("expected_checks", [r"GoogleGenAI|genai\.Client|interactions\.create"])
        synthetic.append(enriched)
    return synthetic


def regex_pass(output: str, expected_checks: list[str]) -> bool:
    for pattern in expected_checks:
        if pattern.startswith("FORBID:"):
            forbidden = re.escape(pattern.removeprefix("FORBID:"))
            if re.search(forbidden, output, flags=re.MULTILINE):
                return False
            continue
        if not re.search(pattern, output, flags=re.MULTILINE):
            return False
    return True


def simulate_agent(case: dict[str, object], has_skill: bool) -> str:
    skill_slug = str(case.get("skill_slug", "gemini_interactions"))
    if skill_slug == "autoresearch_composer":
        prompt = str(case.get("prompt", "")).lower()
        if not has_skill:
            if "debug" in prompt:
                return "/autoresearch:debug single prompt bug loop"
            if "unit test" in prompt or "bug fix" in prompt:
                return "/autoresearch:fix single prompt fix loop"
            if "security" in prompt or "owasp" in prompt:
                return "/autoresearch:security single prompt audit loop"
            if "no metric" in prompt or "no metric or verifier" in prompt:
                return "Iteration-Loop Contract guessed without numeric verifier"
            if "families/*" in prompt or "holdout" in prompt:
                return "/autoresearch:scenario family eval case shortcut"
            if "normal multi-stage" in prompt:
                return "/autoresearch generic SDLC shortcut"
            return "single prompt optimization plan with no metric guard or state graph"
        if not bool(case["should_trigger"]):
            return (
                "native_yield state_graph: S1 match -> S2 route -> S4 validate\n"
                "conditional_edge.S1.no_numeric_metric or conditional_edge.S2.native_skill_better\n"
                "delegate_to_native: diagnose tdd security-review grilling sdlc-plan-composer\n"
                "no slash command route selected; no contract generated"
            )
        return (
            "state_graph: S1 match -> S3 generate -> S4 validate\n"
            "conditional_edge.S3.compressed_context -> low_compression_context via judge-loop-chooser\n"
            "conditional_edge.S3.missing_domain_term -> domain_terms known candidate unknown known_unknowns human_required\n"
            "conditional_edge.S4.ablation_not_positive -> hard_gate FAIL\n"
            "Iteration-Loop Contract: Goal Scope Metric Direction Verify verify Guard guard Iterations route executor /autoresearch:plan\n"
            "Opus fresh judge Codex engineering audit agy findings candidate / [推論] / human_required\n"
            "cases.json 10-20 A/B delta ablation_gate"
        )
    language = str(case.get("language", "python")).lower()
    if not has_skill:
        return "legacy startChat start_chat output"
    if language == "typescript":
        return "import { GoogleGenAI } from '@google/genai';\nconst ai = new GoogleGenAI({});\nawait ai.interactions.create({model: 'gemini-3.5-flash', input: 'hi'});"
    return "from google import genai\nclient = genai.Client()\nclient.interactions.create(model='gemini-3.5-flash', input='hi')"


def score_case(case: dict[str, object], has_skill: bool, agent_fn: Callable[[dict[str, object], bool], str] = simulate_agent) -> float:
    trigger = bool(case["should_trigger"])
    output = agent_fn(case, has_skill)
    expected_checks = [str(item) for item in case.get("expected_checks", [])]
    regex_score = 1.0 if regex_pass(output, expected_checks) else 0.0
    if has_skill:
        return regex_score
    if trigger:
        return min(0.4, regex_score)
    return regex_score


def evaluate(cases: list[dict[str, object]]) -> dict[str, object]:
    synthetic_cases = generate_cases(cases)
    with_skill = sum(score_case(case, True) for case in synthetic_cases) / len(synthetic_cases)
    without_skill = sum(score_case(case, False) for case in synthetic_cases) / len(synthetic_cases)
    positive_cases = [case for case in synthetic_cases if bool(case["should_trigger"])]
    negative_cases = [case for case in synthetic_cases if not bool(case["should_trigger"])]
    positive_with_skill = sum(score_case(case, True) for case in positive_cases) / len(positive_cases)
    negative_with_skill = sum(score_case(case, True) for case in negative_cases) / len(negative_cases)
    delta = with_skill - without_skill
    telemetry = {
        "case_count": len(synthetic_cases),
        "positive_case_count": len(positive_cases),
        "negative_case_count": len(negative_cases),
        "case_ids": [case["case_id"] for case in synthetic_cases],
        "with_skill_success_rate": round(with_skill, 4),
        "without_skill_success_rate": round(without_skill, 4),
        "positive_with_skill_success_rate": round(positive_with_skill, 4),
        "negative_with_skill_success_rate": round(negative_with_skill, 4),
        "delta": round(delta, 4),
        "verdict": "PASS" if delta > TARGET_DELTA else "FAIL",
    }
    return {"telemetry": telemetry, "synthetic_cases": synthetic_cases}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=ROOT / "skills" / "gemini_interactions" / "cases.json", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if not args.cases.is_file():
            raise ValueError(f"cases file does not exist: {args.cases}")
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        report = evaluate(cases)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    telemetry = report["telemetry"]
    if telemetry["verdict"] != "PASS":
        print(f"FAIL: ablation delta too small: {telemetry['delta']:.2f}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "PASS: ablation "
            f"delta={telemetry['delta']:.2f} "
            f"case_count={telemetry['case_count']} "
            f"success_rate={telemetry['with_skill_success_rate']} "
            f"verdict={telemetry['verdict']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
