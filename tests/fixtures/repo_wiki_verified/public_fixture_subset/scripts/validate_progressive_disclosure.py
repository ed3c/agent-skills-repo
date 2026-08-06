#!/usr/bin/env python3
"""Validate progressive disclosure boundaries for skills."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path


ROOT = Path(os.environ.get("PROGRESSIVE_DISCLOSURE_ROOT", Path(__file__).resolve().parents[1]))
NOISE = re.compile(r"\b(?:AWS_ACCESS_KEY|GOOGLE_APPLICATION_CREDENTIALS|[0-9]{1,3}(?:\.[0-9]{1,3}){3})\b")
REQUIRED_ROOT_SIGNALS = ("WHY:", "HOW:", "WHEN:", "WHEN NOT:")
REQUIRED_ROUTES = (r"references/",)


def validate_skill(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid UTF-8 skill root: {path}") from exc
    references = path.parent / "references"
    failures: list[str] = []
    missing_signals = [token for token in REQUIRED_ROOT_SIGNALS if token not in text]
    for token in missing_signals:
        failures.append(f"missing progressive root signal: {token}")
    for pattern in REQUIRED_ROUTES:
        if not re.search(pattern, text):
            failures.append(f"missing route pattern: {pattern}")
    if not references.is_dir():
        failures.append("missing references directory")
    if NOISE.search(text):
        failures.append("deployment detail leaked into root skill")
    return {
        "skill": str(path.relative_to(ROOT)),
        "failure_count": len(failures),
        "failures": failures,
        "verdict": "PASS" if not failures else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        results = [validate_skill(skill) for skill in sorted((ROOT / "skills").glob("*/skills.md"))]
    except Exception as exc:
        print(f"FAIL: progressive disclosure validation error: {exc}", file=sys.stderr)
        return 2
    failures = [item for item in results if item["verdict"] != "PASS"]
    report = {"validated_skills": len(results), "results": results, "verdict": "PASS" if not failures else "FAIL"}
    if failures:
        if args.json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            print("FAIL: progressive disclosure check failed", file=sys.stderr)
            print("\n".join(f"{item['skill']}: {item['failures']}" for item in failures), file=sys.stderr)
        sys.exit(2)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print("PASS: progressive disclosure boundaries hold")
    sys.exit(0)


if __name__ == "__main__":
    raise SystemExit(main())
