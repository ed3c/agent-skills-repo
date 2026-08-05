#!/usr/bin/env python3
"""Generate deterministic 117-case Interactions API regex fixtures."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCENARIOS = (
    "multi-turn assistant",
    "background session",
    "low latency routing",
    "tool invocation",
    "streaming response",
    "session variable storage",
    "resume conversation",
    "error recovery",
    "typed config",
    "migration from v2 chat",
)


def language_for(index: int) -> str:
    return "typescript" if index % 2 else "python"


def expected_checks(language: str) -> list[str]:
    if language == "typescript":
        return [
            r"import\s+\{\s*GoogleGenAI\s*\}\s+from\s+['\"]@google/genai['\"]",
            r"\.interactions\.create\(",
            r"FORBID:\.startChat\(",
            r"FORBID:new\s+GeminiChat\(",
            r"FORBID:Interactions\.createSession\(",
        ]
    return [
        r"from\s+google\s+import\s+genai",
        r"genai\.Client\(",
        r"\.interactions\.create\(",
        r"FORBID:\.start_chat\(",
        r"FORBID:gemini\.interactions\.create_session\(",
    ]


def generate_cases(total: int = 117) -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    for index in range(1, total + 1):
        language = language_for(index)
        scenario = SCENARIOS[(index - 1) % len(SCENARIOS)]
        prompt = (
            f"Case {index}: build a {scenario} using the newest Gemini Interactions API; "
            "avoid legacy v2 chat naming even when the request mentions chat/session continuity."
        )
        cases.append(
            {
                "case_id": f"P11-{index:03d}",
                "prompt": prompt,
                "language": language,
                "should_trigger": True,
                "expected_checks": expected_checks(language),
            }
        )
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--total", default=117, type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.total != 117:
        print("FAIL: production P11 matrix must contain exactly 117 cases", file=sys.stderr)
        return 2
    cases = generate_cases(args.total)
    language_counts = {lang: sum(1 for case in cases if case["language"] == lang) for lang in ("typescript", "python")}
    payload = {"case_count": len(cases), "language_counts": language_counts, "cases": cases}
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    elif args.json:
        print(text, end="")
    print(f"PASS: generated synthetic_cases={len(cases)} typescript={language_counts['typescript']} python={language_counts['python']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
