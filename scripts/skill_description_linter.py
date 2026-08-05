#!/usr/bin/env python3
"""Lint skill descriptions for dense route boundaries."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(os.environ.get("SKILL_DESCRIPTION_LINTER_ROOT", Path(__file__).resolve().parents[1]))
REQUIRED = ("WHY:", "HOW:", "WHEN:", "WHEN NOT:")
FORBIDDEN = ("please", "recommended", "should", "easy to use", "best practice")


def main() -> int:
    failed: list[str] = []
    try:
        for path in sorted((ROOT / "skills").glob("*/skills.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(f"invalid UTF-8 skill description: {path}") from exc
            words = text.replace("\n", " ").split()
            missing = [token for token in REQUIRED if token not in text]
            fluff = [token for token in FORBIDDEN if token in text.lower()]
            if len(words) > 200 or missing or fluff:
                failed.append(f"{path}: words={len(words)} missing={missing} fluff={fluff}")
    except Exception as exc:
        print(f"FAIL: skill description lint error: {exc}", file=sys.stderr)
        return 2
    if failed:
        print("FAIL: skill description lint failed", file=sys.stderr)
        print("\n".join(failed), file=sys.stderr)
        return 2
    print("PASS: skill descriptions are dense and bounded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
