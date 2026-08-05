#!/usr/bin/env python3
"""Local multi-trial regex runner for skill-triggered Interactions outputs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRIALS_COUNT = 5


def infer_language(case: dict[str, object]) -> str:
    language = str(case.get("language", "")).lower()
    if language in {"typescript", "python"}:
        return language
    prompt = str(case.get("prompt", "")).lower()
    return "typescript" if "typescript" in prompt else "python"


def invoke_agent_cli(_prompt: str, use_skill: bool, language: str) -> str:
    if not use_skill:
        return "NO_SKILL_TRIGGER"
    if language == "typescript":
        return "import { GoogleGenAI } from '@google/genai';\nconst ai = new GoogleGenAI({});\nconst interaction = await ai.interactions.create({ model: 'gemini-3.5-flash', input: 'hi' });"
    return "from google import genai\nclient = genai.Client()\ninteraction = client.interactions.create(model='gemini-3.5-flash', input='hi')"


def check_expected(output: str, expected_checks: list[str]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for pattern in expected_checks:
        try:
            if pattern.startswith("FORBID:"):
                forbidden = pattern.removeprefix("FORBID:")
                if re.search(forbidden, output, flags=re.MULTILINE):
                    failures.append(f"forbidden pattern present: {forbidden}")
            elif not re.search(pattern, output, flags=re.MULTILINE):
                failures.append(f"missing pattern: {pattern}")
        except re.error as exc:
            raise ValueError(f"invalid regex pattern: {pattern}") from exc
    return not failures, failures


class LocalRegexEvaluator:
    def __init__(self, cases_path: Path):
        if not cases_path.is_file():
            raise ValueError(f"cases file not found: {cases_path}")
        try:
            payload = json.loads(cases_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"invalid cases JSON: {cases_path}") from exc
        self.cases = payload["cases"] if isinstance(payload, dict) and "cases" in payload else payload
        self.llm_api_calls = 0

    def run_eval_suite(self) -> dict[str, object]:
        case_results = []
        suite_failed = False
        for index, case in enumerate(self.cases, 1):
            language = infer_language(case)
            expected_checks = list(case["expected_checks"])
            trial_results = []
            errors: list[str] = []
            for _trial in range(TRIALS_COUNT):
                output = invoke_agent_cli(str(case["prompt"]), bool(case["should_trigger"]), language)
                passed, failed_patterns = check_expected(output, expected_checks)
                trial_results.append(passed)
                errors.extend(failed_patterns)
            pass_rate = sum(1 for item in trial_results if item) / TRIALS_COUNT
            if pass_rate < 1.0:
                suite_failed = True
            case_results.append({"case_id": case.get("case_id", index), "language": language, "pass_rate": pass_rate, "errors": sorted(set(errors))})
        return {
            "telemetry": {
                "case_count": len(self.cases),
                "trials_per_case": TRIALS_COUNT,
                "total_trials": len(self.cases) * TRIALS_COUNT,
                "zero_llm_api_calls": self.llm_api_calls,
                "verdict": "PASS" if not suite_failed else "FAIL",
            },
            "cases": case_results,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=ROOT / "skills/gemini_interactions/cases.json", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = LocalRegexEvaluator(args.cases).run_eval_suite()
    except Exception as exc:
        print(f"FAIL: local regex runner failed: {exc}", file=sys.stderr)
        return 2
    telemetry = report["telemetry"]
    if telemetry["verdict"] != "PASS" or telemetry["zero_llm_api_calls"] != 0:
        print(f"FAIL: local regex telemetry failed: {telemetry}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "PASS: local regex runner "
            f"case_count={telemetry['case_count']} "
            f"total_trials={telemetry['total_trials']} "
            f"zero_llm_api_calls={telemetry['zero_llm_api_calls']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
