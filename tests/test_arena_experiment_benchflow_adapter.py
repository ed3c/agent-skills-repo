from __future__ import annotations

import copy
import json
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from skill_arena.experiment import (
    generate_plan,
    replay_bundle,
    run_experiment,
    sign_plan,
)
from skill_arena.experiment.benchflow_adapter import (
    BenchFlowExperimentAdapter,
    BenchFlowRuntimePolicy,
    compute_skill_artifact_digest,
    fetch_github_model_catalog_evidence,
    load_runtime_policy,
    prepare_benchflow_runtime,
    summarize_paired_bundle,
    validate_catalog_evidence,
)
from skill_arena.experiment.model import (
    ExperimentError,
    SPEC_SCHEMA,
    canonical_bytes,
    sha256_bytes,
)
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data/arena/benchflow-dialogue-parser-github-models.json"
IMAGE = "sha256:" + "1" * 64
TASK_DIGEST = "sha256:" + "2" * 64
TOKEN = "ghp_abcdefghijklmnopqrstuvwxyz123456"
NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def skillsbench_validator(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    calls: list[Path] = []
    package = types.ModuleType("arena_adapters")
    module = types.ModuleType("arena_adapters.skillsbench")

    def validate_bundle_directory(path: Path | str) -> None:
        calls.append(Path(path))
        if (Path(path) / "reject-structural").exists():
            raise ValueError("planted structural drift")

    module.validate_bundle_directory = validate_bundle_directory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "arena_adapters", package)
    monkeypatch.setitem(sys.modules, "arena_adapters.skillsbench", module)
    return calls


def policy() -> BenchFlowRuntimePolicy:
    return load_runtime_policy(POLICY_PATH)


def make_bundle(tmp_path: Path, runtime_policy: BenchFlowRuntimePolicy) -> Path:
    root = tmp_path / "bundle"
    skill = root / "package/environment/skills/dialogue-graph"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Dialogue graph\n", encoding="utf-8")
    (skill / "reference.txt").write_text("edge labels\n", encoding="utf-8")
    environment = root / "package/environment"
    (environment / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    manifest = {
        "schema_version": "arena-task-bundle@1",
        "bundle_digest": runtime_policy.task_bundle_digest,
        "task": {
            "task_id": runtime_policy.task_id,
            "role": runtime_policy.task_family,
            "network_mode": runtime_policy.network_policy,
        },
        "skill_injection": {"skill_names": [runtime_policy.candidate_skill_name]},
    }
    (root / "bundle.json").write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )
    return root


class Response:
    def __init__(self, value: object):
        self.value = value

    def read(self) -> bytes:
        return json.dumps(self.value).encode("utf-8")


def catalog_rows(runtime_policy: BenchFlowRuntimePolicy) -> list[dict[str, object]]:
    return [
        {
            "id": runtime_policy.catalog_model_id,
            "name": "OpenAI GPT-4.1-mini",
            "publisher": "OpenAI",
            "registry": "azure-openai",
            "version": "2025-04-14",
            "capabilities": ["tool-calling", "streaming"],
            "limits": {"max_input_tokens": 1048576, "max_output_tokens": 32768},
            "rate_limit_tier": "low",
        }
    ]


def catalog_evidence(runtime_policy: BenchFlowRuntimePolicy) -> dict[str, object]:
    return fetch_github_model_catalog_evidence(
        token=TOKEN,
        policy=runtime_policy,
        fetched_at=NOW,
        opener=lambda request, timeout: Response(catalog_rows(runtime_policy)),
    )


class FakeRunner:
    def __init__(self, *, image: str = IMAGE, mode: str = "success") -> None:
        self.image = image
        self.mode = mode
        self.calls: list[dict[str, object]] = []
        self.configs: list[dict[str, object]] = []

    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(
            {"command": list(command), "cwd": cwd, "env": dict(env or {}), "timeout": timeout}
        )
        if command[:2] == ["docker", "build"]:
            return subprocess.CompletedProcess(command, 0, "built\n", "")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, self.image + "\n", "")
        if command[:2] == ["docker", "version"]:
            return subprocess.CompletedProcess(command, 0, "27.3.1\n", "")
        if command and command[0] == "/usr/bin/time":
            if self.mode == "process-timeout":
                raise subprocess.TimeoutExpired(command, timeout or 1, output="", stderr="")
            config_path = Path(command[-1])
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            assert isinstance(config, dict)
            self.configs.append(config)
            self._write_job(config, command)
            return subprocess.CompletedProcess(
                command,
                0 if self.mode not in {"nonzero"} else 7,
                f"runner token={TOKEN}\n",
                "stderr Authorization: Bearer " + TOKEN + "\n",
            )
        raise AssertionError(f"unexpected command: {command}")

    def _write_job(self, config: dict[str, object], command: list[str]) -> None:
        jobs = Path(str(config["jobs_dir"]))
        rollout = jobs / "run" / "dialogue-parser__fake"
        verifier = rollout / "verifier"
        trajectory = rollout / "trajectory"
        verifier.mkdir(parents=True)
        trajectory.mkdir(parents=True)
        skill_mode = str(config["skill_mode"])
        reward: float | None = 1.0
        error = error_category = verifier_error = verifier_category = None
        if self.mode == "task-failure":
            reward = 0.0
        elif self.mode == "verifier-failure":
            reward = None
            verifier_error = "verifier crashed"
            verifier_category = "verifier_failure"
        elif self.mode == "timeout":
            reward = None
            error = "agent timed out"
            error_category = "timeout"
        elif self.mode == "transport":
            reward = None
            error = "closed stdout"
            error_category = "pipe_closed"
        elif self.mode == "provider-auth":
            reward = None
            error = "provider auth failed"
            error_category = "provider_auth"
        elif self.mode == "missing-result":
            return
        elif self.mode == "usage-unavailable":
            pass
        elif self.mode == "unknown-tool":
            pass
        include = skill_mode == "with-skill"
        result = {
            "task_name": "dialogue-parser",
            "rollout_name": "dialogue-parser__fake",
            "rewards": None if reward is None else {"reward": reward},
            "agent": "pi-acp",
            "agent_name": "pi",
            "model": "github-models/openai/gpt-4.1-mini",
            "skill_mode": skill_mode,
            "skill_source": "task" if include else "none",
            "requested_skills_dir": None,
            "effective_skills_dir": "/skills" if include else None,
            "skills_sandbox_dir": "/home/agent/.agents/skills" if include else None,
            "include_task_skills": include,
            "n_tool_calls": 1,
            "n_skill_invocations": 1 if include else 0,
            "n_prompts": 1,
            "agent_result": {
                "n_tool_calls": 1,
                "n_skill_invocations": 1 if include else 0,
                "n_prompts": 1,
                "n_input_tokens": 100,
                "n_output_tokens": 20,
                "n_cache_read_tokens": 3,
                "n_cache_creation_tokens": 2,
                "total_tokens": 125,
                "cost_usd": 0.00125,
                "usage_source": (
                    "unavailable" if self.mode == "usage-unavailable" else "provider_response"
                ),
                "price_source": "github-models-catalog",
                "api_key": TOKEN,
            },
            "usage_tracking": {
                "requested": "required",
                "status": "active",
                "environment": "docker",
                "endpoint_kind": "host",
                "usage_source": (
                    "unavailable" if self.mode == "usage-unavailable" else "provider_response"
                ),
            },
            "error": error,
            "error_category": error_category,
            "verifier_error": verifier_error,
            "verifier_error_category": verifier_category,
            "export_error": None,
            "started_at": "2026-08-09 00:00:00",
            "finished_at": "2026-08-09 00:00:02",
            "timing": {"environment_setup": 0.2, "verifier": 0.1, "total": 2.0},
            "task_digest": TASK_DIGEST,
        }
        (rollout / "result.json").write_text(
            json.dumps(result, sort_keys=True) + "\n", encoding="utf-8"
        )
        tool = "network.fetch" if self.mode == "unknown-tool" else "bash"
        events = [
            {
                "type": "tool_call",
                "name": tool,
                "arguments": {"command": "echo ok", "authorization": TOKEN},
            },
            {"type": "agent_message", "text": "done " + TOKEN},
        ]
        (trajectory / "acp_trajectory.jsonl").write_text(
            "".join(json.dumps(item) + "\n" for item in events), encoding="utf-8"
        )
        (verifier / "reward.txt").write_text(
            "" if reward is None else f"{reward}\n", encoding="utf-8"
        )
        (verifier / "ctrf.json").write_text(
            json.dumps({"results": {"summary": {"tests": 1}, "tests": []}}) + "\n",
            encoding="utf-8",
        )
        time_path = Path(command[command.index("-o") + 1])
        time_path.write_text(
            "User time (seconds): 0.10\n"
            "System time (seconds): 0.02\n"
            "Maximum resident set size (kbytes): 1024\n",
            encoding="utf-8",
        )


def prepared(
    tmp_path: Path,
    runtime_policy: BenchFlowRuntimePolicy,
    runner: FakeRunner,
) -> tuple[Path, dict[str, object], dict[str, object]]:
    bundle = make_bundle(tmp_path, runtime_policy)
    catalog = catalog_evidence(runtime_policy)
    preparation = prepare_benchflow_runtime(
        bundle_dir=bundle,
        policy=runtime_policy,
        catalog_evidence=catalog,
        image_tag="prepare:test",
        runner=runner,
    )
    return bundle, catalog, preparation


def invocation(
    runtime_policy: BenchFlowRuntimePolicy,
    preparation: Mapping[str, object],
    arm: str,
) -> dict[str, object]:
    spec = {
        "schema_version": SPEC_SCHEMA,
        "experiment_id": "adapter-test",
        "tasks": [
            {
                "task_id": runtime_policy.task_id,
                "task_family": runtime_policy.task_family,
                "task_bundle_digest": runtime_policy.task_bundle_digest,
            }
        ],
        "candidate_skill_artifact_digest": preparation[
            "candidate_skill_artifact_digest"
        ],
        "placebo_skill_artifact_digest": None,
        "agent_id": runtime_policy.agent,
        "model_id": runtime_policy.model,
        "harness_id": "benchflow",
        "harness_version": runtime_policy.benchflow_version,
        "sandbox_profile_id": runtime_policy.sandbox_profile_id,
        "environment_image_digest": preparation["environment_image_digest"],
        "policy_digest": preparation["effective_policy_digest"],
        "network_policy": runtime_policy.network_policy,
        "allowed_tools_digest": runtime_policy.allowed_tools_digest,
        "repetitions": runtime_policy.repetitions,
        "randomization_seed": 9,
        "agent_seed_mode": "unavailable",
        "preregistered_at": "2026-08-09T00:00:00Z",
    }
    plan = generate_plan(spec)
    return next(row for row in plan["invocations"] if row["arm"] == arm)


def adapter(
    tmp_path: Path,
    runtime_policy: BenchFlowRuntimePolicy,
    runner: FakeRunner,
) -> tuple[BenchFlowExperimentAdapter, dict[str, object]]:
    bundle, catalog, preparation = prepared(tmp_path, runtime_policy, runner)
    bench = tmp_path / "bench"
    bench.write_text("#!/bin/sh\n", encoding="utf-8")
    return (
        BenchFlowExperimentAdapter(
            bench_bin=bench,
            bundle_dir=bundle,
            policy=runtime_policy,
            catalog_evidence=catalog,
            preparation=preparation,
            github_token=TOKEN,
            image_tag_prefix="test-image",
            runner=runner,
            base_environment={"OPENAI_API_KEY": "must-not-leak", "PATH": "/usr/bin"},
        ),
        preparation,
    )


def raw_public(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def test_runtime_policy_is_fail_closed() -> None:
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    assert policy().max_retries == 0
    for field, value, match in (
        ("max_retries", 1, "retries"),
        ("model", "openai/gpt-4.1-mini", "github-models"),
        ("repetitions", 2, "three"),
        ("concurrency", 2, "one invocation"),
    ):
        changed = dict(raw)
        changed[field] = value
        with pytest.raises(ExperimentError, match=match):
            BenchFlowRuntimePolicy.from_mapping(changed)


def test_catalog_evidence_is_exact_and_digested() -> None:
    runtime_policy = policy()
    evidence = catalog_evidence(runtime_policy)
    assert evidence["model_id"] == "openai/gpt-4.1-mini"
    assert evidence["capabilities"] == ["streaming", "tool-calling"]
    assert validate_catalog_evidence(evidence, runtime_policy) == evidence
    tampered = dict(evidence, version="changed")
    with pytest.raises(ExperimentError, match="digest"):
        validate_catalog_evidence(tampered, runtime_policy)
    with pytest.raises(ExperimentError, match="exactly one"):
        fetch_github_model_catalog_evidence(
            token=TOKEN,
            policy=runtime_policy,
            fetched_at=NOW,
            opener=lambda request, timeout: Response(catalog_rows(runtime_policy) * 2),
        )


def test_skill_digest_and_full_bundle_validator_are_load_bearing(
    tmp_path: Path, skillsbench_validator: list[Path]
) -> None:
    runtime_policy = policy()
    bundle = make_bundle(tmp_path, runtime_policy)
    digest = compute_skill_artifact_digest(
        bundle / "package/environment/skills/dialogue-graph"
    )
    assert digest.startswith("sha256:")
    runner = FakeRunner()
    prepare_benchflow_runtime(
        bundle_dir=bundle,
        policy=runtime_policy,
        catalog_evidence=catalog_evidence(runtime_policy),
        image_tag="prepare:test",
        runner=runner,
    )
    assert skillsbench_validator == [bundle]
    (bundle / "reject-structural").write_text("drift", encoding="utf-8")
    with pytest.raises(ExperimentError, match="structural validation"):
        prepare_benchflow_runtime(
            bundle_dir=bundle,
            policy=runtime_policy,
            catalog_evidence=catalog_evidence(runtime_policy),
            image_tag="prepare:test",
            runner=runner,
        )
    (bundle / "reject-structural").unlink()
    target = bundle / "package/environment/skills/dialogue-graph/reference.txt"
    target.unlink()
    target.symlink_to(tmp_path / "outside")
    with pytest.raises(ExperimentError, match="symlink"):
        compute_skill_artifact_digest(target.parent)


def test_adapter_writes_single_attempt_paired_configs_and_scrubs_secrets(
    tmp_path: Path,
) -> None:
    runtime_policy = policy()
    runner = FakeRunner()
    subject, preparation = adapter(tmp_path, runtime_policy, runner)
    for arm in ("baseline", "candidate"):
        workspace = tmp_path / arm
        workspace.mkdir()
        capture = subject.execute(invocation(runtime_policy, preparation, arm), workspace)
        assert capture.classification == "succeeded"
        assert capture.reward == 1.0
        joined = capture.stdout + capture.stderr + b"".join(capture.artifacts.values())
        assert TOKEN.encode() not in joined
    assert [item["skill_mode"] for item in runner.configs] == ["no-skill", "with-skill"]
    for config in runner.configs:
        assert config["max_retries"] == 0
        assert config["concurrency"] == 1
        assert config["usage_tracking"] == "required"
    assert runner.configs[0]["agent_env"] == {}
    assert runner.configs[1]["agent_env"] == {"BENCHFLOW_SKILL_NUDGE": "name"}
    bench_calls = [call for call in runner.calls if call["command"][0] == "/usr/bin/time"]
    assert all("OPENAI_API_KEY" not in call["env"] for call in bench_calls)
    assert all(call["env"]["GITHUB_TOKEN"] == TOKEN for call in bench_calls)


@pytest.mark.parametrize(
    ("mode", "classification"),
    [
        ("task-failure", "task_failure"),
        ("verifier-failure", "verifier_failure"),
        ("timeout", "timeout"),
        ("transport", "transport_loss"),
        ("provider-auth", "infrastructure_failure"),
        ("unknown-tool", "infrastructure_failure"),
        ("missing-result", "infrastructure_failure"),
        ("process-timeout", "timeout"),
    ],
)
def test_adapter_preserves_distinct_failure_classes(
    tmp_path: Path, mode: str, classification: str
) -> None:
    runtime_policy = policy()
    runner = FakeRunner(mode=mode)
    subject, preparation = adapter(tmp_path, runtime_policy, runner)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    capture = subject.execute(invocation(runtime_policy, preparation, "baseline"), workspace)
    assert capture.classification == classification
    if classification not in {"task_failure", "succeeded"}:
        assert capture.reward is None


def test_required_usage_and_image_drift_fail_closed(tmp_path: Path) -> None:
    runtime_policy = policy()
    runner = FakeRunner(mode="usage-unavailable")
    subject, preparation = adapter(tmp_path, runtime_policy, runner)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with pytest.raises(ExperimentError, match="telemetry"):
        subject.execute(invocation(runtime_policy, preparation, "baseline"), workspace)

    drift = FakeRunner(image="sha256:" + "9" * 64)
    # Build preparation with the expected image, then execute through a drifting runner.
    bundle = make_bundle(tmp_path / "drift", runtime_policy)
    initial = FakeRunner(image=IMAGE)
    catalog = catalog_evidence(runtime_policy)
    preparation = prepare_benchflow_runtime(
        bundle_dir=bundle,
        policy=runtime_policy,
        catalog_evidence=catalog,
        image_tag="prepare:test",
        runner=initial,
    )
    bench = tmp_path / "drift-bench"
    bench.write_text("#!/bin/sh\n", encoding="utf-8")
    subject = BenchFlowExperimentAdapter(
        bench_bin=bench,
        bundle_dir=bundle,
        policy=runtime_policy,
        catalog_evidence=catalog,
        preparation=preparation,
        github_token=TOKEN,
        image_tag_prefix="drift-image",
        runner=drift,
        base_environment={},
    )
    workspace = tmp_path / "drift-workspace"
    workspace.mkdir()
    with pytest.raises(ExperimentError, match="drifted"):
        subject.execute(invocation(runtime_policy, preparation, "baseline"), workspace)


def test_end_to_end_bundle_replay_and_paired_summary(tmp_path: Path) -> None:
    runtime_policy = policy()
    runner = FakeRunner()
    subject, preparation = adapter(tmp_path, runtime_policy, runner)
    spec = {
        "schema_version": SPEC_SCHEMA,
        "experiment_id": "real-adapter-control",
        "tasks": [
            {
                "task_id": runtime_policy.task_id,
                "task_family": runtime_policy.task_family,
                "task_bundle_digest": runtime_policy.task_bundle_digest,
            }
        ],
        "candidate_skill_artifact_digest": preparation[
            "candidate_skill_artifact_digest"
        ],
        "placebo_skill_artifact_digest": None,
        "agent_id": runtime_policy.agent,
        "model_id": runtime_policy.model,
        "harness_id": "benchflow",
        "harness_version": runtime_policy.benchflow_version,
        "sandbox_profile_id": runtime_policy.sandbox_profile_id,
        "environment_image_digest": preparation["environment_image_digest"],
        "policy_digest": preparation["effective_policy_digest"],
        "network_policy": runtime_policy.network_policy,
        "allowed_tools_digest": runtime_policy.allowed_tools_digest,
        "repetitions": 3,
        "randomization_seed": 11,
        "agent_seed_mode": "unavailable",
        "preregistered_at": "2026-08-09T00:00:00Z",
    }
    plan_key = Ed25519PrivateKey.generate()
    bundle_key = Ed25519PrivateKey.generate()
    envelope = sign_plan(
        generate_plan(spec), private_key=plan_key, issuer_key_id="plan-key"
    )
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    bundle = run_experiment(
        envelope,
        trusted_plan_keys={"plan-key": raw_public(plan_key)},
        adapter=subject,
        output_dir=runtime_root / "bundles",
        bundle_private_key=bundle_key,
        bundle_issuer_key_id="bundle-key",
        now_fn=lambda: NOW,
        nonce_fn=iter([f"nonce-{index}" for index in range(6)]).__next__,
    )
    summary = summarize_paired_bundle(bundle)
    assert summary["ranking_claim_allowed"] is False
    assert summary["arms"]["baseline"]["planned"] == 3
    assert summary["arms"]["candidate"]["planned"] == 3
    assert summary["observed_reward_lift"] == 0.0
    assert len(runner.configs) == 6

    replay = replay_bundle(
        bundle,
        trusted_plan_keys={"plan-key": raw_public(plan_key)},
        trusted_bundle_keys={"bundle-key": raw_public(bundle_key)},
    )
    catalog = catalog_evidence(runtime_policy)
    (runtime_root / "runtime-policy.json").write_text(
        POLICY_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    for name, value in (
        ("model-catalog-evidence.json", catalog),
        ("preparation.json", preparation),
        ("preregistered-plan-envelope.json", envelope),
        ("replay-result.json", replay),
        ("paired-result.json", summary),
    ):
        (runtime_root / name).write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    keys = {
        "schema_version": "arena-experiment-public-keys@1",
        "keys": [
            {
                "key_id": "plan-key",
                "purpose": "experiment-preregistration",
                "algorithm": "Ed25519",
                "public_key_base64": __import__("base64").b64encode(raw_public(plan_key)).decode(),
            },
            {
                "key_id": "bundle-key",
                "purpose": "experiment-bundle",
                "algorithm": "Ed25519",
                "public_key_base64": __import__("base64").b64encode(raw_public(bundle_key)).decode(),
            },
        ],
    }
    (runtime_root / "public-keys.json").write_text(
        json.dumps(keys, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    runtime_summary = {
        "schema_version": "arena-real-runtime-summary@1",
        "status": "complete",
        "run_identity": "test-run",
        "task_id": runtime_policy.task_id,
        "agent": runtime_policy.agent,
        "model": runtime_policy.model,
        "model_catalog_version": catalog["version"],
        "bundle_path": bundle.relative_to(runtime_root).as_posix(),
        "bundle_manifest_hash": replay["manifest_hash"],
        "plan_digest": replay["plan_digest"],
        "preparation_digest": preparation["preparation_digest"],
        "paired_result_digest": summary["result_digest"],
        "started_at": "2026-08-09T00:00:00Z",
        "completed_at": "2026-08-09T00:01:00Z",
        "ranking_claim_allowed": False,
    }
    (runtime_root / "runtime-summary.json").write_text(
        json.dumps(runtime_summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    files = []
    for path in sorted(runtime_root.rglob("*")):
        if path.is_file() and path.name != "artifact-manifest.json":
            data = path.read_bytes()
            files.append({
                "path": path.relative_to(runtime_root).as_posix(),
                "sha256": sha256_bytes(data),
                "size_bytes": len(data),
            })
    manifest_without = {
        "schema_version": "arena-runtime-artifact-manifest@1",
        "files": files,
    }
    artifact_manifest = {
        **manifest_without,
        "manifest_digest": sha256_bytes(canonical_bytes(manifest_without)),
    }
    (runtime_root / "artifact-manifest.json").write_text(
        json.dumps(artifact_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checked = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/check_arena_benchflow_runtime.py"),
            "--root",
            str(runtime_root),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert checked.returncode == 0, checked.stdout + checked.stderr


def test_runtime_schema_accepts_all_static_and_materialized_documents(tmp_path: Path) -> None:
    schema = json.loads(
        (ROOT / "contracts/arena-benchflow-runtime.schema.json").read_text(encoding="utf-8")
    )
    validator = Draft202012Validator(schema)
    runtime_policy = policy()
    runner = FakeRunner()
    _, catalog, preparation = prepared(tmp_path, runtime_policy, runner)
    for value in (runtime_policy.as_mapping(), catalog, preparation):
        assert list(validator.iter_errors(value)) == []

    changed = runtime_policy.as_mapping()
    changed["max_retries"] = 1
    assert list(validator.iter_errors(changed))
