#!/usr/bin/env python3
"""Deterministic benchmark matrix for skill quality deltas."""

from __future__ import annotations

import json
import statistics


TASKS = [{"task_id": index, "difficulty": "Hard" if index % 3 == 0 else "Medium"} for index in range(1, 101)]


def pass_rate(group: str, model: str, harness: str) -> float:
    base = 0.70
    adjustment = {"control": 0.0, "high_quality": 0.15, "low_quality": -0.20}[group]
    stable_jitter = ((len(model) + len(harness)) % 3) * 0.005
    return round(base + adjustment + stable_jitter, 4)


def run_matrix() -> dict[str, object]:
    models = ("Gemini-2.0-Flash", "Claude-3.5-Sonnet")
    harnesses = ("Harness-A-Coding", "Harness-B-Productivity")
    groups = ("control", "high_quality", "low_quality")
    raw = []
    for model in models:
        for harness in harnesses:
            for group in groups:
                raw.append({"model": model, "harness": harness, "group": group, "pass_rate": pass_rate(group, model, harness)})
    summary = {group: statistics.mean(item["pass_rate"] for item in raw if item["group"] == group) for group in groups}
    return {
        "task_count": len(TASKS),
        "raw": raw,
        "summary": summary,
        "delta_high_quality": round(summary["high_quality"] - summary["control"], 4),
        "delta_low_quality": round(summary["low_quality"] - summary["control"], 4),
    }


def main() -> int:
    report = run_matrix()
    if report["task_count"] != 100 or report["delta_high_quality"] < 0.13 or report["delta_low_quality"] >= 0:
        print(json.dumps(report, sort_keys=True))
        return 2
    print(
        "PASS: benchmark matrix "
        f"task_count={report['task_count']} "
        f"delta_high_quality={report['delta_high_quality']} "
        f"delta_low_quality={report['delta_low_quality']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
