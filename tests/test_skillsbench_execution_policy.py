from __future__ import annotations

from pathlib import Path

from arena_adapters.skillsbench.execution import load_probe_policy

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "data/skillsbench/execution-probe-policy.json"


def test_repository_probe_policy_contains_only_scored_task_outputs() -> None:
    tasks = load_probe_policy(POLICY)

    assert tasks["dialogue-parser"].output_paths == (
        "/app/dialogue.dot",
        "/app/dialogue.json",
    )
    assert tasks["pdf-excel-diff"].output_paths == ("/root/diff_report.json",)
    assert tasks["weighted-gdp-calc"].output_paths == ("/root/gdp.xlsx",)

    all_outputs = {
        output_path
        for task in tasks.values()
        for output_path in task.output_paths
    }
    assert "/app/solution.py" not in all_outputs
