#!/usr/bin/env python3
"""Validate the machine-readable SKILL.md Arena roadmap."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.roadmap import RoadmapReadError, validate_roadmap_files  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--roadmap",
        type=Path,
        default=ROOT / "data/project/skill-arena-roadmap.json",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=ROOT / "contracts/skill-arena-roadmap.schema.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        errors = validate_roadmap_files(args.roadmap, args.schema)
    except RoadmapReadError as exc:
        print(f"FAIL: Skill Arena roadmap is unreadable: {exc}", file=sys.stderr)
        return 2

    if errors:
        print("FAIL: Skill Arena roadmap contract", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2

    print("PASS: Skill Arena roadmap schema, dependencies, and status invariants")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
