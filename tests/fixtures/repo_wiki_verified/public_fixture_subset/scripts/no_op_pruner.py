#!/usr/bin/env python3
"""Dry-run no-op phrase detector for skill prompts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NO_OPS = ("clean code", "easy to read", "best practice", "highly recommended", "please")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args()
    findings: list[str] = []
    try:
        paths = sorted((args.repo_root / "skills").glob("*/skills.md"))
        for path in paths:
            try:
                text = path.read_text(encoding="utf-8").lower()
            except UnicodeDecodeError as exc:
                raise ValueError(f"invalid UTF-8 skill asset: {path}") from exc
            for phrase in NO_OPS:
                if phrase in text:
                    findings.append(f"{path}: {phrase}")
    except Exception as exc:
        print(f"FAIL: no-op prompt scan failed: {exc}", file=sys.stderr)
        return 2
    if findings:
        print("FAIL: no-op prompt phrases found", file=sys.stderr)
        print("\n".join(findings), file=sys.stderr)
        return 2
    print("PASS: no-op prompt scan is clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
