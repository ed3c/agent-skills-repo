from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, cast

from .common import canonical_bytes, read_json_object, sha256_bytes
from .models import SkillsBenchAdapterError

PROBE_POLICY_SCHEMA_VERSION = "skillsbench-execution-probe-policy@1"
EXECUTION_EVIDENCE_SCHEMA_VERSION = "skillsbench-execution-evidence@1"


@dataclass(frozen=True)
class ProbeTask:
    task_id: str
    output_paths: tuple[str, ...]


def _exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise SkillsBenchAdapterError(
            f"{label} fields differ: expected={sorted(expected)}"
            f" actual={sorted(value)}"
        )


def _absolute_output_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SkillsBenchAdapterError(f"{label} must be a non-empty absolute path")
    path = PurePosixPath(value)
    if (
        not path.is_absolute()
        or value == "/"
        or "." in path.parts
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise SkillsBenchAdapterError(
            f"{label} must be a normalized absolute POSIX path: {value!r}"
        )
    return value


def load_probe_policy(path: Path | str) -> dict[str, ProbeTask]:
    raw = read_json_object(path)
    _exact_keys(raw, {"schema_version", "tasks"}, "probe policy")
    if raw.get("schema_version") != PROBE_POLICY_SCHEMA_VERSION:
        raise SkillsBenchAdapterError("execution probe policy schema is unsupported")
    raw_tasks = raw.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise SkillsBenchAdapterError("execution probe policy has no tasks")
    tasks: dict[str, ProbeTask] = {}
    ordered_ids: list[str] = []
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, dict):
            raise SkillsBenchAdapterError(f"probe policy task {index} is not an object")
        _exact_keys(raw_task, {"task_id", "output_paths"}, f"probe policy task {index}")
        task_id = raw_task.get("task_id")
        raw_paths = raw_task.get("output_paths")
        if not isinstance(task_id, str) or not task_id:
            raise SkillsBenchAdapterError(f"probe policy task {index} task_id is invalid")
        if task_id in tasks:
            raise SkillsBenchAdapterError(f"duplicate execution probe task: {task_id}")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise SkillsBenchAdapterError(
                f"execution probe task {task_id} has no output paths"
            )
        paths = tuple(
            _absolute_output_path(value, f"execution probe task {task_id} output")
            for value in raw_paths
        )
        if paths != tuple(sorted(paths)) or len(set(paths)) != len(paths):
            raise SkillsBenchAdapterError(
                f"execution probe task {task_id} output paths must be sorted and unique"
            )
        tasks[task_id] = ProbeTask(task_id=task_id, output_paths=paths)
        ordered_ids.append(task_id)
    if ordered_ids != sorted(ordered_ids):
        raise SkillsBenchAdapterError("execution probe tasks must be sorted by task_id")
    return tasks


def probe_output_paths(policy_path: Path | str, task_id: str) -> tuple[str, ...]:
    tasks = load_probe_policy(policy_path)
    try:
        return tasks[task_id].output_paths
    except KeyError as exc:
        raise SkillsBenchAdapterError(
            f"execution probe policy does not contain task: {task_id}"
        ) from exc


def _regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise SkillsBenchAdapterError(f"{label} must be a regular file: {path}")
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode):
        raise SkillsBenchAdapterError(f"{label} is not a regular file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SkillsBenchAdapterError(f"cannot read {label}: {path}: {exc}") from exc


def fixture_identity(
    fixture_root: Path | str,
    output_paths: tuple[str, ...],
) -> tuple[str, list[dict[str, object]]]:
    root = Path(fixture_root)
    if root.is_symlink() or not root.is_dir():
        raise SkillsBenchAdapterError(f"probe fixture root is absent: {root}")
    resolved_root = root.resolve(strict=True)
    entries: list[dict[str, object]] = []
    expected_relative: set[str] = set()
    for output_path in output_paths:
        relative = output_path.lstrip("/")
        expected_relative.add(relative)
        host_path = root / relative
        try:
            host_path.resolve(strict=True).relative_to(resolved_root)
        except (OSError, ValueError) as exc:
            raise SkillsBenchAdapterError(
                f"probe fixture output escapes or is absent: {output_path}"
            ) from exc
        data = _regular_file(host_path, f"probe fixture output {output_path}")
        entries.append(
            {
                "path": output_path,
                "sha256": sha256_bytes(data),
                "size_bytes": len(data),
            }
        )

    actual_relative: set[str] = set()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise SkillsBenchAdapterError(
                f"probe fixture contains a symlink: {relative}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise SkillsBenchAdapterError(
                f"probe fixture contains a special file: {relative}"
            )
        actual_relative.add(relative)
    if actual_relative != expected_relative:
        raise SkillsBenchAdapterError(
            "probe fixture file set differs from policy:"
            f" expected={sorted(expected_relative)} actual={sorted(actual_relative)}"
        )
    return sha256_bytes(canonical_bytes({"files": entries})), entries


def _unique_file(root: Path, name: str, label: str) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise SkillsBenchAdapterError(f"{label} root is absent: {root}")
    matches = sorted(root.rglob(name))
    if len(matches) != 1:
        raise SkillsBenchAdapterError(
            f"{label} must contain exactly one {name}: found {len(matches)}"
        )
    _regular_file(matches[0], f"{label} {name}")
    return matches[0]


def _reward(path: Path, label: str) -> float:
    data = _regular_file(path, label)
    try:
        value = float(data.decode("utf-8").strip())
    except (UnicodeDecodeError, ValueError) as exc:
        raise SkillsBenchAdapterError(f"{label} is not a scalar reward") from exc
    return value


def _diagnostics(
    ctrf_path: Path,
    *,
    reward: float,
    execution_error: object = None,
    verifier_error: object = None,
) -> tuple[str, str, dict[str, object]]:
    raw = read_json_object(ctrf_path)
    results = raw.get("results")
    if not isinstance(results, dict):
        raise SkillsBenchAdapterError("CTRF results must be an object")
    summary = results.get("summary")
    tests = results.get("tests")
    if not isinstance(summary, dict) or not isinstance(tests, list):
        raise SkillsBenchAdapterError("CTRF summary/tests are malformed")
    count_fields = ("tests", "passed", "failed", "skipped", "pending", "other")
    counts: dict[str, int] = {}
    for field in count_fields:
        value = summary.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise SkillsBenchAdapterError(f"CTRF summary field is invalid: {field}")
        counts[field] = value
    canonical_tests: list[dict[str, object]] = []
    for index, raw_test in enumerate(tests):
        if not isinstance(raw_test, dict):
            raise SkillsBenchAdapterError(f"CTRF test {index} is not an object")
        name = raw_test.get("name")
        status = raw_test.get("status")
        file_path = raw_test.get("file_path")
        if not isinstance(name, str) or not name:
            raise SkillsBenchAdapterError(f"CTRF test {index} name is invalid")
        if not isinstance(status, str) or not status:
            raise SkillsBenchAdapterError(f"CTRF test {index} status is invalid")
        if file_path is not None and not isinstance(file_path, str):
            raise SkillsBenchAdapterError(f"CTRF test {index} file_path is invalid")
        canonical_tests.append(
            {"name": name, "status": status, "file_path": file_path}
        )
    canonical_tests.sort(
        key=lambda item: (
            cast(str, item["name"]),
            cast(str | None, item["file_path"]) or "",
            cast(str, item["status"]),
        )
    )
    if counts["tests"] != len(canonical_tests):
        raise SkillsBenchAdapterError("CTRF summary test count does not match tests")
    canonical: dict[str, object] = {
        "summary": counts,
        "tests": canonical_tests,
    }
    if execution_error is not None or verifier_error is not None:
        diagnostics_class = "error"
    elif reward == 1.0 and counts["failed"] == 0 and counts["other"] == 0:
        diagnostics_class = "pass_with_skips" if counts["skipped"] else "pass"
    else:
        diagnostics_class = "fail"
    return (
        diagnostics_class,
        sha256_bytes(canonical_bytes(canonical)),
        canonical,
    )


def parse_benchflow_oracle_job(jobs_root: Path | str) -> dict[str, object]:
    root = Path(jobs_root)
    result_path = _unique_file(root, "result.json", "BenchFlow jobs")
    rollout_root = result_path.parent
    reward_path = rollout_root / "verifier" / "reward.txt"
    ctrf_path = rollout_root / "verifier" / "ctrf.json"
    reward = _reward(reward_path, "BenchFlow verifier reward")
    result = read_json_object(result_path)
    if result.get("agent_name") != "oracle":
        raise SkillsBenchAdapterError("BenchFlow result agent_name is not oracle")
    if result.get("environment_name") != "docker":
        raise SkillsBenchAdapterError("BenchFlow result environment_name is not docker")
    rewards = result.get("rewards")
    if not isinstance(rewards, dict):
        raise SkillsBenchAdapterError("BenchFlow result rewards are absent")
    result_reward = rewards.get("reward")
    if (
        not isinstance(result_reward, (int, float))
        or isinstance(result_reward, bool)
        or float(result_reward) != reward
    ):
        raise SkillsBenchAdapterError(
            "BenchFlow result reward does not match verifier reward.txt"
        )
    task_digest = result.get("task_digest")
    if (
        not isinstance(task_digest, str)
        or not task_digest.startswith("sha256:")
        or len(task_digest) != 71
    ):
        raise SkillsBenchAdapterError("BenchFlow result task_digest is invalid")
    execution_error = result.get("error")
    verifier_error = result.get("verifier_error")
    for label, value in (
        ("error", execution_error),
        ("verifier_error", verifier_error),
    ):
        if value is not None and not isinstance(value, str):
            raise SkillsBenchAdapterError(f"BenchFlow result {label} is invalid")
    diagnostics_class, diagnostics_digest, _ = _diagnostics(
        ctrf_path,
        reward=reward,
        execution_error=execution_error,
        verifier_error=verifier_error,
    )
    return {
        "result_digest": sha256_bytes(_regular_file(result_path, "BenchFlow result")),
        "task_digest": task_digest,
        "reward": reward,
        "error": execution_error,
        "verifier_error": verifier_error,
        "diagnostics_class": diagnostics_class,
        "diagnostics_digest": diagnostics_digest,
    }


def parse_verifier_probe(logs_root: Path | str) -> dict[str, object]:
    root = Path(logs_root)
    reward_path = _unique_file(root, "reward.txt", "verifier probe logs")
    ctrf_path = _unique_file(root, "ctrf.json", "verifier probe logs")
    reward = _reward(reward_path, "verifier probe reward")
    diagnostics_class, diagnostics_digest, _ = _diagnostics(
        ctrf_path,
        reward=reward,
    )
    return {
        "reward": reward,
        "diagnostics_class": diagnostics_class,
        "diagnostics_digest": diagnostics_digest,
    }


def build_execution_evidence(
    *,
    bundle_manifest: Mapping[str, object],
    surface: str,
    jobs_root: Path | str,
    fixture_root: Path | str,
    verifier_logs_root: Path | str,
    output_paths: tuple[str, ...],
    benchflow_version: str,
    task_check_passed: bool,
) -> dict[str, object]:
    if surface not in {"upstream", "normalized"}:
        raise SkillsBenchAdapterError(f"execution evidence surface is invalid: {surface!r}")
    if not benchflow_version:
        raise SkillsBenchAdapterError("BenchFlow version must be explicit")
    task = bundle_manifest.get("task")
    upstream = bundle_manifest.get("upstream")
    bundle_digest = bundle_manifest.get("bundle_digest")
    if not isinstance(task, Mapping) or not isinstance(upstream, Mapping):
        raise SkillsBenchAdapterError("bundle manifest lacks task/upstream identity")
    task_id = task.get("task_id")
    repository = upstream.get("repository")
    commit = upstream.get("commit")
    if not all(isinstance(value, str) and value for value in (task_id, repository, commit)):
        raise SkillsBenchAdapterError("bundle task/upstream identity is invalid")
    if not isinstance(bundle_digest, str) or not bundle_digest.startswith("sha256:"):
        raise SkillsBenchAdapterError("bundle manifest digest is invalid")
    if task_check_passed is not True:
        raise SkillsBenchAdapterError("task check must pass before execution evidence")

    fixture_digest, _ = fixture_identity(fixture_root, output_paths)
    oracle = parse_benchflow_oracle_job(jobs_root)
    probe = parse_verifier_probe(verifier_logs_root)
    evidence: dict[str, object] = {
        "schema_version": EXECUTION_EVIDENCE_SCHEMA_VERSION,
        "task_id": task_id,
        "bundle_digest": bundle_digest,
        "surface": surface,
        "upstream": {
            "repository": repository,
            "commit": commit,
        },
        "execution": {
            "benchflow_version": benchflow_version,
            "agent": "oracle",
            "sandbox": "docker",
        },
        "task_check_passed": True,
        "oracle": {
            "result_digest": oracle["result_digest"],
            "task_digest": oracle["task_digest"],
            "reward": oracle["reward"],
            "error": oracle["error"],
            "verifier_error": oracle["verifier_error"],
        },
        "verifier_probe": {
            "input_digest": fixture_digest,
            "reward": probe["reward"],
            "diagnostics_class": probe["diagnostics_class"],
            "diagnostics_digest": probe["diagnostics_digest"],
        },
    }
    evidence["evidence_digest"] = sha256_bytes(canonical_bytes(evidence))
    return evidence


def write_execution_evidence(path: Path | str, evidence: Mapping[str, object]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
