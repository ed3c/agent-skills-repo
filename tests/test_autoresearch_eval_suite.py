from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.evals
ROOT = Path(__file__).resolve().parents[1]


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_pr_golden_dataset_passes_local_judge() -> None:
    result = run_script(
        "scripts/eval_autoresearch_composer.py",
        "--dataset",
        "data/autoresearch_golden/pr_golden_set.json",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "cloud_judge_enabled=false" in result.stdout


def test_nightly_golden_dataset_passes_local_judge() -> None:
    result = run_script(
        "scripts/eval_autoresearch_composer.py",
        "--dataset",
        "data/autoresearch_golden/nightly_golden_set.jsonl",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "PASS: autoresearch eval suite" in result.stdout


def test_local_trace_samples_are_validated() -> None:
    result = run_script("scripts/sample_autoresearch_traces.py")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "sample_count=3" in result.stdout
