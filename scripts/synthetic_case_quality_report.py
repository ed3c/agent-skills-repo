#!/usr/bin/env python3
"""Report P11 synthetic case corpus quality without upgrading canary truth."""

from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_generator():
    path = ROOT / "scripts/synthetic_case_generator.py"
    spec = importlib.util.spec_from_file_location("synthetic_case_generator", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load generator: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalize_prompt(prompt: str) -> str:
    return " ".join(prompt.lower().replace("case ", "").split())


def scenario_from_prompt(prompt: str) -> str:
    marker = "build a "
    suffix = " using"
    if marker in prompt and suffix in prompt:
        return prompt.split(marker, 1)[1].split(suffix, 1)[0]
    return "unknown"


def max_template_similarity(prompts: list[str]) -> float:
    if len(prompts) < 2:
        return 0.0
    best = 0.0
    normalized = [normalize_prompt(prompt) for prompt in prompts]
    for left in range(len(normalized)):
        for right in range(left + 1, len(normalized)):
            best = max(best, SequenceMatcher(None, normalized[left], normalized[right]).ratio())
    return round(best, 4)


def analyze_cases(cases: list[dict[str, object]]) -> dict[str, object]:
    prompts = [str(case["prompt"]) for case in cases]
    scenarios = [scenario_from_prompt(prompt) for prompt in prompts]
    expected_sets = [tuple(case.get("expected_checks", [])) for case in cases]
    language_counts = Counter(str(case.get("language", "unknown")) for case in cases)
    should_trigger = Counter(bool(case.get("should_trigger", False)) for case in cases)
    stats = {
        "case_count": len(cases),
        "unique_prompts": len(set(prompts)),
        "unique_scenarios": len(set(scenarios)),
        "language_counts": dict(sorted(language_counts.items())),
        "unique_expected_check_sets": len(set(expected_sets)),
        "trigger_true": should_trigger[True],
        "trigger_false": should_trigger[False],
        "negative_cases": should_trigger[False],
        "legacy_bait_cases": sum("legacy" in prompt.lower() or "v2" in prompt.lower() or "startchat" in prompt.lower() for prompt in prompts),
        "max_template_similarity_ratio": max_template_similarity(prompts),
        "real_agent_runs": 0,
        "llm_api_calls": 0,
    }
    insufficient_reasons = []
    if stats["unique_expected_check_sets"] < 12:
        insufficient_reasons.append("unique_expected_check_sets_lt_12")
    if stats["negative_cases"] < 20:
        insufficient_reasons.append("negative_cases_lt_20")
    if stats["trigger_false"] < 10:
        insufficient_reasons.append("non_trigger_cases_lt_10")
    if stats["max_template_similarity_ratio"] > 0.35:
        insufficient_reasons.append("template_similarity_gt_0_35")
    stats["quality_status"] = "insufficient" if insufficient_reasons else "admissible-candidate"
    stats["insufficient_reasons"] = insufficient_reasons
    return stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    cases = load_generator().generate_cases(117)
    stats = analyze_cases(cases)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    else:
        print(
            "PASS: synthetic case quality perceived "
            f"quality_status={stats['quality_status']} "
            f"case_count={stats['case_count']} "
            f"unique_scenarios={stats['unique_scenarios']} "
            f"unique_expected_check_sets={stats['unique_expected_check_sets']} "
            f"negative_cases={stats['negative_cases']} "
            f"max_template_similarity_ratio={stats['max_template_similarity_ratio']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
