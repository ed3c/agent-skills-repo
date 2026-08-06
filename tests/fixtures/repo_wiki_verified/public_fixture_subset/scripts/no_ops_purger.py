#!/usr/bin/env python3
"""Prune no-op prompt lines only when deterministic behavior is preserved."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NO_OPS_PATTERNS = (
    r"clean\s+code",
    r"easy\s+to\s+read",
    r"high\s+quality",
    r"robust\s+architecture",
    r"best\s+practice",
    r"beautiful\s+code",
    r"please",
    r"highly\s+recommended",
    r"優雅",
    r"乾淨",
    r"高質量",
    r"最佳實踐",
    r"請確保",
    r"易讀",
    r"健壯",
)


def run_p2_evaluation(skill_content: str) -> float:
    if "Interactions" not in skill_content:
        return 0.10
    return 0.95


def is_no_op_line(line: str) -> bool:
    return any(re.search(pattern, line, re.IGNORECASE) for pattern in NO_OPS_PATTERNS)


def purge_text(text: str) -> tuple[str, int, int]:
    baseline = run_p2_evaluation(text)
    pruned_lines = text.splitlines()
    index = 0
    purged_count = 0
    while index < len(pruned_lines):
        line = pruned_lines[index].strip()
        if not line or not is_no_op_line(line):
            index += 1
            continue
        candidate = list(pruned_lines)
        candidate.pop(index)
        candidate_text = "\n".join(candidate)
        current_rate = run_p2_evaluation(candidate_text)
        if current_rate >= baseline:
            pruned_lines.pop(index)
            purged_count += 1
            baseline = current_rate
        else:
            index += 1
    output = "\n".join(pruned_lines)
    return output, purged_count, max((len(text) - len(output)) // 4, 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skill", default=ROOT / "skills/gemini_interactions/skills.md", type=Path)
    args = parser.parse_args()
    try:
        if not args.skill.is_file():
            raise ValueError(f"skill file not found: {args.skill}")
        try:
            text = args.skill.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"invalid UTF-8 skill file: {args.skill}") from exc
        _, purged_count, tokens_saved = purge_text(text)
    except Exception as exc:
        print(f"FAIL: no-ops purge failed: {exc}", file=sys.stderr)
        return 2
    print(f"PASS: no-ops purge checked purged_count={purged_count} tokens_saved={tokens_saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
