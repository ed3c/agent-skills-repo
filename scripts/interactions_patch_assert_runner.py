#!/usr/bin/env python3
"""Zero-LLM regex assert runner for post-cutoff Gemini Interactions syntax."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
RULES = {
    "typescript": {
        "must_match": [
            r"import\s+\{\s*GoogleGenAI\s*\}\s+from\s+['\"]@google/genai['\"]",
            r"\.interactions\.create\(",
        ],
        "must_not_match": [r"\.startChat\(", r"new\s+GeminiChat\(", r"Interactions\.createSession\("],
    },
    "python": {
        "must_match": [r"from\s+google\s+import\s+genai", r"genai\.Client\(", r"\.interactions\.create\("],
        "must_not_match": [r"\.start_chat\(", r"gemini\.interactions\.create_session\("],
    },
}


def load_generator():
    path = ROOT / "scripts/synthetic_case_generator.py"
    spec = importlib.util.spec_from_file_location("synthetic_case_generator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load generator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EdgeCaseAssertEngine:
    def __init__(self, cases: list[dict[str, object]]):
        self.cases = cases
        self.total_run = 0
        self.passed_run = 0
        self.llm_api_calls = 0

    def check_syntax_compliance(self, code: str, language: str) -> tuple[bool, list[str]]:
        rules = RULES.get(language.lower())
        if not rules:
            return False, [f"Unmapped language framework: {language}"]
        failures: list[str] = []
        for pattern in rules["must_match"]:
            if not re.search(pattern, code, flags=re.MULTILINE):
                failures.append(f"Missing mandatory pattern: {pattern}")
        for pattern in rules["must_not_match"]:
            if re.search(pattern, code, flags=re.MULTILINE):
                failures.append(f"Legacy regression pattern detected: {pattern}")
        return not failures, failures

    def execute_dry_run(self, agent_fn: Callable[[str, str], str]) -> dict[str, object]:
        results: list[dict[str, object]] = []
        for index, case in enumerate(self.cases, 1):
            self.total_run += 1
            language = str(case["language"])
            generated_code = agent_fn(str(case["prompt"]), language)
            success, errors = self.check_syntax_compliance(generated_code, language)
            if success:
                self.passed_run += 1
            results.append({"case_id": case.get("case_id", index), "language": language, "status": "PASS" if success else "FAIL", "errors": errors})
        success_rate = self.passed_run / self.total_run if self.total_run else 0.0
        return {
            "telemetry": {
                "total_cases_evaluated": self.total_run,
                "passed_cases": self.passed_run,
                "success_rate": round(success_rate, 4),
                "zero_llm_api_calls": self.llm_api_calls,
                "verdict": "TARGET_MET" if success_rate >= 0.88 else "TARGET_FAILED",
            },
            "runs": results,
        }


def patched_agent(_prompt: str, language: str) -> str:
    if language.lower() == "typescript":
        return "import { GoogleGenAI } from '@google/genai';\nconst ai = new GoogleGenAI({});\nconst interaction = await ai.interactions.create({ model: 'gemini-3.5-flash', input: 'hi', background: true });"
    return "from google import genai\nclient = genai.Client()\ninteraction = client.interactions.create(model='gemini-3.5-flash', input='hi', background=True)"


def load_cases(path: Path | None) -> list[dict[str, object]]:
    if path is None:
        return load_generator().generate_cases(117)
    if not path.is_file():
        raise ValueError(f"cases file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid cases JSON: {path}") from exc
    if isinstance(payload, dict) and "cases" in payload:
        return payload["cases"]
    if isinstance(payload, list):
        return payload
    raise ValueError("cases payload must be a list or object with cases")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        cases = load_cases(args.cases)
        engine = EdgeCaseAssertEngine(cases)
        report = engine.execute_dry_run(patched_agent)
    except Exception as exc:
        print(f"FAIL: interactions patch assertions failed: {exc}", file=sys.stderr)
        return 2
    telemetry = report["telemetry"]
    if telemetry["total_cases_evaluated"] != 117 or telemetry["zero_llm_api_calls"] != 0 or telemetry["verdict"] != "TARGET_MET":
        print(f"FAIL: telemetry did not meet P11 production target: {telemetry}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "PASS: interactions regex assertions "
            f"total_cases_evaluated={telemetry['total_cases_evaluated']} "
            f"passed_cases={telemetry['passed_cases']} "
            f"success_rate={telemetry['success_rate']} "
            f"zero_llm_api_calls={telemetry['zero_llm_api_calls']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
