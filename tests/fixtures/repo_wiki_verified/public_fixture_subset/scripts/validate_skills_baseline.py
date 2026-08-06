#!/usr/bin/env python3
"""Git-aware GCR baseline validator for modified skill case assets."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import validator


ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = re.compile(r"^[AM\s]{1,2}\s+(skills/.*/cases\.json)$")


def modified_case_files() -> list[Path]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        if "not a git repository" in result.stderr:
            return []
        raise RuntimeError(result.stderr.strip() or "git status failed")
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        match = re.search(CASE_PATH, line)
        if match:
            paths.append(ROOT / match.group(1))
    return paths


def all_case_files() -> list[Path]:
    return sorted((ROOT / "skills").glob("*/cases.json"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="scan every skills/*/cases.json file")
    args = parser.parse_args()
    try:
        paths = all_case_files() if args.all else modified_case_files()
        if not paths:
            paths = all_case_files()
        if not paths:
            print("FAIL: no skills/*/cases.json files found", file=sys.stderr)
            return 2
        for path in paths:
            validator.validate_cases(path)
    except Exception as exc:
        print(f"FAIL: skill baseline validation failed: {exc}", file=sys.stderr)
        return 2
    print(f"PASS: skill baseline validated cases={len(paths)} mode={'all' if args.all else 'git-diff-or-all'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
