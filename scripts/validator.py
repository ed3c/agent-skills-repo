#!/usr/bin/env python3
"""Validate skill cases.json files with the GCR minimum baseline."""

from __future__ import annotations

import json
import argparse
import os
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


ROOT = Path(os.environ.get("VALIDATOR_ROOT", Path(__file__).resolve().parents[1]))
WEAK_PATTERNS = {"", ".*", ".+", "^.*$", "^.+$"}


def fail(message: str) -> None:
    raise ValueError(message)


def validate_case(case: dict[str, Any], index: int) -> None:
    for field in ("id", "prompt", "should_trigger", "expected_checks"):
        if field not in case:
            fail(f"case {index} missing field: {field}")
    if not isinstance(case["prompt"], str) or not case["prompt"].strip():
        fail(f"case {index} prompt must be non-empty text")
    if not isinstance(case["should_trigger"], bool):
        fail(f"case {index} should_trigger must be boolean")
    checks = case["expected_checks"]
    if not isinstance(checks, list) or not checks:
        fail(f"case {index} expected_checks must be a non-empty list")
    for check in checks:
        if not isinstance(check, str) or check.strip() in WEAK_PATTERNS:
            fail(f"case {index} has weak expected check: {check!r}")


def validate_cases(path: Path) -> None:
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"{path}: invalid UTF-8 cases JSON")
    try:
        cases = json.loads(text)
    except Exception as exc:
        fail(f"{path}: invalid cases JSON")
    if not isinstance(cases, list):
        fail(f"{path}: root must be a JSON array")
    if not 10 <= len(cases) <= 20:
        fail(f"{path}: expected 10-20 cases, got {len(cases)}")
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict):
            fail(f"case {index} must be an object")
        validate_case(case, index)
    positives = sum(1 for case in cases if case["should_trigger"])
    negatives = len(cases) - positives
    if positives < 5 or negatives < 5:
        fail(f"{path}: expected 5+ positive and 5+ negative cases")
    prompts = [case["prompt"].lower() for case in cases]
    for left in range(len(prompts)):
        for right in range(left + 1, len(prompts)):
            if SequenceMatcher(None, prompts[left], prompts[right]).ratio() > 0.85:
                fail(f"{path}: near-duplicate prompts: {left + 1}, {right + 1}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="emit machine-readable telemetry")
    args = parser.parse_args()
    paths = sorted((ROOT / "skills").glob("*/cases.json"))
    if not paths:
        print("FAIL: no skills/*/cases.json files found", file=sys.stderr)
        return 2
    try:
        for path in paths:
            validate_cases(path)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"validated_case_files": len(paths), "verdict": "PASS"}, sort_keys=True))
        return 0
    print(f"PASS: validated {len(paths)} skill case file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
