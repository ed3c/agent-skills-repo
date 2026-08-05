#!/usr/bin/env python3
"""Validate that skill instructions use goal/constraints instead of brittle steps."""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SEQUENCES = (
    r"step\s*\d+",
    r"第一步",
    r"第二步",
    r"第三步",
    r"第四步",
    r"\bfirstly\b",
    r"\bsecondly\b",
    r"\bthen\b",
    r"\bfinally\b",
    r"\b首先\b",
    r"\b然後\b",
    r"\b最後\b",
    r"\b接著\b",
)
REQUIRED_BLOCKS = ("GOAL:", "CONSTRAINTS:")
MANDATORY_CONSTRAINTS = (
    (r"language|typescript|python", "CODE_LANGUAGE"),
    (r"ssl|tls|protocol|secure", "SECURITY_PROTOCOL"),
)


def validate_text(content: str) -> list[str]:
    failures: list[str] = []
    for block in REQUIRED_BLOCKS:
        if block not in content:
            failures.append(f"missing required block: {block}")
    found_sequences = []
    for pattern in FORBIDDEN_SEQUENCES:
        found_sequences.extend(re.findall(pattern, content, re.IGNORECASE))
    if found_sequences:
        failures.append(f"sequential workflow traces: {sorted(set(found_sequences))}")
    constraints_section = content.split("CONSTRAINTS:")[-1]
    for pattern, token in MANDATORY_CONSTRAINTS:
        if not re.search(pattern, constraints_section, re.IGNORECASE):
            failures.append(f"missing mandatory constraint: {token}")
    return failures


def validate_path(path: Path) -> list[str]:
    if not path.is_file():
        raise ValueError(f"goal constraints file does not exist: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid UTF-8 goal constraints file: {path}") from exc
    return validate_text(text)


def selftest() -> None:
    bad = """
GOAL: Update port.
CONSTRAINTS:
- CODE_LANGUAGE: Must use TypeScript.
Workflow:
Step 1: Read the file.
Step 2: Replace the port.
"""
    good = """
GOAL: Update the database connection port.
CONSTRAINTS:
- CODE_LANGUAGE: Must use TypeScript.
- SECURITY_PROTOCOL: Must use SSL/TLS.
"""
    with tempfile.TemporaryDirectory(prefix="goal-constraints.") as tmp:
        bad_path = Path(tmp) / "bad.md"
        good_path = Path(tmp) / "good.md"
        bad_path.write_text(bad, encoding="utf-8")
        good_path.write_text(good, encoding="utf-8")
        if not validate_path(bad_path):
            raise AssertionError("bad fixture unexpectedly passed")
        if validate_path(good_path):
            raise AssertionError("good fixture unexpectedly failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    try:
        if not args.paths:
            selftest()
        else:
            failures = {str(path): validate_path(path) for path in args.paths}
            failures = {path: errors for path, errors in failures.items() if errors}
            if failures:
                for path, errors in failures.items():
                    print(f"FAIL: {path}: {'; '.join(errors)}", file=sys.stderr)
                return 2
    except Exception as exc:
        print(f"FAIL: goal constraints validation failed: {exc}", file=sys.stderr)
        return 2
    print("PASS: goal constraints validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
