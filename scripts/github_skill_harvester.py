#!/usr/bin/env python3
"""Harvest local skill asset metrics without network access."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NO_OPS_KEYWORDS = ("please", "highly recommended", "best practice", "easy to read", "beautiful code")


def calculate_entropy(text: str) -> float:
    clean_text = re.sub(r"\s+", "", text)
    if not clean_text:
        return 0.0
    frequencies: dict[str, int] = {}
    for char in clean_text:
        frequencies[char] = frequencies.get(char, 0) + 1
    entropy = 0.0
    for count in frequencies.values():
        p = count / len(clean_text)
        entropy -= p * math.log2(p)
    return entropy


def harvest_single_file(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    words = text.split()
    word_count = len(words)
    lowered = text.lower()
    no_ops_count = sum(lowered.count(term) for term in NO_OPS_KEYWORDS)
    no_ops_density = no_ops_count / max(word_count, 1)
    entropy = calculate_entropy(text)
    return {
        "file_path": str(path),
        "word_count": word_count,
        "no_ops_density": round(no_ops_density, 4),
        "entropy": round(entropy, 4),
        "is_suspected_ai_generated": word_count > 500 or (entropy < 4.5 and no_ops_density > 0.05),
    }


def scan_ecosystem(workspace: Path, max_workers: int = 8) -> list[dict[str, object]]:
    skill_files = sorted(workspace.glob("skills/*/skills.md"))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(harvest_single_file, skill_files))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=ROOT, type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        results = scan_ecosystem(args.workspace)
    except Exception as exc:
        print(f"FAIL: harvest failed: {exc}", file=sys.stderr)
        return 2
    if not results:
        print("FAIL: no skill assets found", file=sys.stderr)
        return 2
    payload = {"skill_count": len(results), "skills": results}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(f"PASS: harvested skill_count={len(results)} suspected_ai={sum(1 for item in results if item['is_suspected_ai_generated'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
