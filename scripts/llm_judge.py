#!/usr/bin/env python3
"""Local parser for LLM judge verdicts with XML-shield and double-lock semantics."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys


def extract_json(text: str) -> dict[str, object]:
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("no JSON object in judge response")
    try:
        return json.loads(
            match.group(0),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-standard JSON constant")),
        )
    except Exception as exc:
        raise ValueError("invalid JSON object in judge response") from exc


def parse_score(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("score must be numeric") from exc
    if not math.isfinite(score):
        raise ValueError("score must be finite")
    return score


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--response", default='{"verdict":"PASS","score":0.91,"reasoning":"clean"}')
    args = parser.parse_args()
    try:
        payload = extract_json(f"<judge_output>{args.response}</judge_output>")
        verdict = str(payload.get("verdict", "")).upper()
        score = parse_score(payload.get("score", 0.0))
        reasoning = str(payload.get("reasoning", "")).lower()
    except Exception as exc:
        print(f"FAIL: malformed judge response: {exc}", file=sys.stderr)
        return 2
    if verdict != "PASS" or score < 0.85 or "breach detected" in reasoning:
        print("FAIL: double-lock judge rejection", file=sys.stderr)
        return 2
    print("PASS: double-lock judge verdict accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
