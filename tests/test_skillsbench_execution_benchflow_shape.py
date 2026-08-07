from __future__ import annotations

import json
from pathlib import Path

import pytest

from arena_adapters.skillsbench.execution import parse_benchflow_oracle_job
from arena_adapters.skillsbench.models import SkillsBenchAdapterError


def _write_current_benchflow_job(
    root: Path,
    *,
    config_environment: str = "docker",
    usage_environment: str = "docker",
) -> Path:
    rollout = root / "evaluation" / "rollout"
    verifier = rollout / "verifier"
    verifier.mkdir(parents=True)

    result = {
        "agent_name": "oracle",
        "task_digest": "sha256:" + "3" * 64,
        "rewards": {"reward": 1.0},
        "error": None,
        "verifier_error": None,
        "usage_tracking": {
            "environment": usage_environment,
            "mode": "off",
        },
    }
    (rollout / "result.json").write_text(
        json.dumps(result, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (rollout / "config.json").write_text(
        json.dumps({"environment": config_environment}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (verifier / "reward.txt").write_text("1\n", encoding="utf-8")
    (verifier / "ctrf.json").write_text(
        json.dumps(
            {
                "results": {
                    "summary": {
                        "tests": 1,
                        "passed": 1,
                        "failed": 0,
                        "skipped": 0,
                        "pending": 0,
                        "other": 0,
                    },
                    "tests": [
                        {
                            "name": "test_output",
                            "status": "passed",
                            "file_path": "/verifier/test_outputs.py",
                        }
                    ],
                }
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def test_benchflow_063_environment_signals_are_accepted(tmp_path: Path) -> None:
    parsed = parse_benchflow_oracle_job(_write_current_benchflow_job(tmp_path / "jobs"))

    assert parsed["reward"] == 1.0
    assert parsed["task_digest"] == "sha256:" + "3" * 64
    assert parsed["diagnostics_class"] == "pass"


def test_conflicting_environment_signals_fail_closed(tmp_path: Path) -> None:
    jobs = _write_current_benchflow_job(
        tmp_path / "jobs",
        config_environment="daytona",
        usage_environment="docker",
    )

    with pytest.raises(SkillsBenchAdapterError, match="not docker"):
        parse_benchflow_oracle_job(jobs)
