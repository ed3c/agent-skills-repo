from __future__ import annotations

import json
from pathlib import Path

import pytest

from arena_adapters.skillsbench.execution import (
    build_execution_evidence,
    fixture_identity,
    load_probe_policy,
    parse_benchflow_oracle_job,
    probe_output_paths,
)
from arena_adapters.skillsbench.models import SkillsBenchAdapterError
from arena_adapters.skillsbench.parity import bind_execution_parity, report_digest


def bundle_manifest() -> dict[str, object]:
    return {
        "schema_version": "arena-task-bundle@1",
        "upstream": {
            "repository": "benchflow-ai/skillsbench",
            "commit": "1" * 40,
        },
        "task": {"task_id": "probe-task", "role": "code"},
        "bundle_digest": "sha256:" + "2" * 64,
    }


def parity_report() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "skillsbench-parity-report@1",
        "task_id": "probe-task",
        "bundle_digest": "sha256:" + "2" * 64,
        "status": "known_loss",
        "ranking_eligible": False,
        "structural": {"status": "equivalent"},
        "execution": {"status": "not_run"},
        "known_losses": ["execution parity evidence is absent"],
    }
    value["report_digest"] = report_digest(value)
    return value


def ctrf(*, failed: int = 0, test_status: str = "passed") -> dict[str, object]:
    return {
        "results": {
            "tool": {"name": "pytest"},
            "summary": {
                "tests": 1,
                "passed": 1 if failed == 0 else 0,
                "failed": failed,
                "skipped": 0,
                "pending": 0,
                "other": 0,
            },
            "tests": [
                {
                    "name": "test_output",
                    "status": test_status,
                    "file_path": "/verifier/test_outputs.py",
                }
            ],
        }
    }


def write_job(
    root: Path,
    *,
    reward: float = 1.0,
    result_reward: float | None = None,
    task_digest: str | None = None,
    rollout_name: str = "source",
    failed: int = 0,
) -> Path:
    rollout = root / "evaluation" / rollout_name
    verifier = rollout / "verifier"
    verifier.mkdir(parents=True)
    result = {
        "agent_name": "oracle",
        "environment_name": "docker",
        "task_digest": task_digest or ("sha256:" + "3" * 64),
        "rewards": {"reward": reward if result_reward is None else result_reward},
        "error": None,
        "verifier_error": None,
        "rollout_name": rollout_name,
        "started_at": "2026-08-06T00:00:00Z",
    }
    (rollout / "result.json").write_text(
        json.dumps(result, sort_keys=True) + "\n", encoding="utf-8"
    )
    (verifier / "reward.txt").write_text(f"{reward}\n", encoding="utf-8")
    (verifier / "ctrf.json").write_text(
        json.dumps(
            ctrf(
                failed=failed,
                test_status="failed" if failed else "passed",
            ),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def write_probe_logs(root: Path, *, reward: float = 1.0, failed: int = 0) -> Path:
    root.mkdir(parents=True)
    (root / "reward.txt").write_text(f"{reward}\n", encoding="utf-8")
    (root / "ctrf.json").write_text(
        json.dumps(
            ctrf(
                failed=failed,
                test_status="failed" if failed else "passed",
            ),
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return root


def write_fixture(root: Path, value: bytes = b"fixture\n") -> Path:
    path = root / "root" / "output.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(value)
    return root


def evidence(
    tmp_path: Path,
    surface: str,
    *,
    fixture_value: bytes = b"fixture\n",
    probe_failed: int = 0,
) -> dict[str, object]:
    jobs = write_job(
        tmp_path / f"jobs-{surface}",
        rollout_name=surface,
    )
    fixture = write_fixture(tmp_path / f"fixture-{surface}", fixture_value)
    logs = write_probe_logs(
        tmp_path / f"logs-{surface}",
        failed=probe_failed,
        reward=0.0 if probe_failed else 1.0,
    )
    return build_execution_evidence(
        bundle_manifest=bundle_manifest(),
        surface=surface,
        jobs_root=jobs,
        fixture_root=fixture,
        verifier_logs_root=logs,
        output_paths=("/root/output.json",),
        benchflow_version="0.6.3",
        task_check_passed=True,
    )


def test_bound_evidence_accepts_different_raw_runs_with_same_semantics(
    tmp_path: Path,
) -> None:
    upstream = evidence(tmp_path, "upstream")
    normalized = evidence(tmp_path, "normalized")
    assert upstream["oracle"]["result_digest"] != normalized["oracle"]["result_digest"]  # type: ignore[index]
    assert upstream["oracle"]["task_digest"] == normalized["oracle"]["task_digest"]  # type: ignore[index]
    assert upstream["verifier_probe"] == normalized["verifier_probe"]

    result = bind_execution_parity(parity_report(), upstream, normalized)

    assert result["status"] == "equivalent"
    assert result["ranking_eligible"] is True


def test_different_fixture_bytes_are_rejected_by_parity(tmp_path: Path) -> None:
    upstream = evidence(tmp_path, "upstream", fixture_value=b"one\n")
    normalized = evidence(tmp_path, "normalized", fixture_value=b"two\n")

    result = bind_execution_parity(parity_report(), upstream, normalized)

    assert result["status"] == "rejected"
    assert result["execution"]["same_probe_input"] is False  # type: ignore[index]


def test_different_verifier_diagnostics_are_rejected(tmp_path: Path) -> None:
    upstream = evidence(tmp_path, "upstream")
    normalized = evidence(tmp_path, "normalized", probe_failed=1)

    result = bind_execution_parity(parity_report(), upstream, normalized)

    assert result["status"] == "rejected"
    assert result["execution"]["diagnostics_digest_equal"] is False  # type: ignore[index]


def test_reward_file_must_match_result_json(tmp_path: Path) -> None:
    jobs = write_job(
        tmp_path / "jobs",
        reward=1.0,
        result_reward=0.0,
    )
    with pytest.raises(SkillsBenchAdapterError, match="does not match"):
        parse_benchflow_oracle_job(jobs)


def test_multiple_result_files_fail_closed(tmp_path: Path) -> None:
    jobs = write_job(tmp_path / "jobs", rollout_name="first")
    write_job(jobs, rollout_name="second")
    with pytest.raises(SkillsBenchAdapterError, match="exactly one result.json"):
        parse_benchflow_oracle_job(jobs)


def test_fixture_policy_rejects_extra_files_and_symlinks(tmp_path: Path) -> None:
    fixture = write_fixture(tmp_path / "fixture")
    (fixture / "extra.txt").write_text("extra\n", encoding="utf-8")
    with pytest.raises(SkillsBenchAdapterError, match="file set differs"):
        fixture_identity(fixture, ("/root/output.json",))

    (fixture / "extra.txt").unlink()
    target = fixture / "root/output.json"
    target.unlink()
    target.symlink_to(tmp_path / "outside")
    with pytest.raises(SkillsBenchAdapterError, match="escapes or is absent"):
        fixture_identity(fixture, ("/root/output.json",))


def test_probe_policy_is_sorted_and_uses_absolute_paths(tmp_path: Path) -> None:
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "schema_version": "skillsbench-execution-probe-policy@1",
                "tasks": [
                    {"task_id": "b", "output_paths": ["/root/b"]},
                    {"task_id": "a", "output_paths": ["relative"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SkillsBenchAdapterError, match="absolute POSIX path"):
        load_probe_policy(policy)

    policy.write_text(
        json.dumps(
            {
                "schema_version": "skillsbench-execution-probe-policy@1",
                "tasks": [
                    {"task_id": "b", "output_paths": ["/root/b"]},
                    {"task_id": "a", "output_paths": ["/root/a"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SkillsBenchAdapterError, match="sorted by task_id"):
        load_probe_policy(policy)

    policy.write_text(
        json.dumps(
            {
                "schema_version": "skillsbench-execution-probe-policy@1",
                "tasks": [
                    {"task_id": "a", "output_paths": ["/root/a"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert probe_output_paths(policy, "a") == ("/root/a",)
