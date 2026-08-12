from __future__ import annotations

import base64, copy, json, shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator

from skill_arena.experiment import (
    ExperimentError, InvocationCapture, generate_plan, replay_bundle,
    run_experiment, sign_plan, verify_plan_envelope,
)
from skill_arena.experiment.model import (
    METRICS_SCHEMA, PLAN_SIGNATURE_DOMAIN, SPEC_SCHEMA, SPEC_SCHEMA_V2, TRAJECTORY_SCHEMA,
    VERIFIER_SCHEMA, canonical_bytes, sha256_bytes, sha256_json,
)

NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)


def pub(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def spec(*, placebo=False) -> dict[str, object]:
    return {
        "schema_version": SPEC_SCHEMA, "experiment_id": "arena-test",
        "tasks": [
            {"task_id": "task-a", "task_family": "code",
             "task_bundle_digest": sha256_bytes(b"task-a")},
            {"task_id": "task-b", "task_family": "document",
             "task_bundle_digest": sha256_bytes(b"task-b")},
        ],
        "candidate_skill_artifact_digest": sha256_bytes(b"candidate"),
        "placebo_skill_artifact_digest": sha256_bytes(b"placebo") if placebo else None,
        "agent_id": "test-agent", "model_id": "test-model",
        "harness_id": "benchflow", "harness_version": "0.6.3",
        "sandbox_profile_id": "docker-test",
        "environment_image_digest": sha256_bytes(b"image"),
        "policy_digest": sha256_bytes(b"policy"), "network_policy": "no-network",
        "allowed_tools_digest": sha256_bytes(b"tools"), "repetitions": 3,
        "randomization_seed": 42, "agent_seed_mode": "deterministic",
        "preregistered_at": "2026-08-07T00:00:00Z",
    }


def version_two_spec() -> dict[str, object]:
    return {
        **spec(),
        "schema_version": SPEC_SCHEMA_V2,
        "baseline_skill_artifact_digest": sha256_bytes(b"baseline-skill"),
        "study_protocol_digest": sha256_bytes(b"frozen-study-protocol"),
    }


def capture(invocation: Mapping[str, object], state="succeeded") -> InvocationCapture:
    reward = 1.0 if state == "succeeded" else (0.0 if state == "task_failure" else None)
    return InvocationCapture(
        classification=state, reward=reward,
        adapter_exit_code=0 if reward is not None else 1,
        error_code=None if state == "succeeded" else f"{state}_observed",
        stdout=b"stdout\n", stderr=b"" if state == "succeeded" else b"failure\n",
        trajectory={"schema_version": TRAJECTORY_SCHEMA,
                    "events": [{"type": "complete", "arm": invocation["arm"]}]},
        verifier={"schema_version": VERIFIER_SCHEMA, "status": state,
                  "reward": reward, "diagnostics_digest": sha256_bytes(state.encode())},
        metrics={"schema_version": METRICS_SCHEMA, "end_to_end_latency_ms": 10,
                 "verifier_latency_ms": 2, "input_tokens": 3, "output_tokens": 4,
                 "tool_tokens": 5, "cost_microunits": 6, "cpu_time_ms": 7,
                 "peak_memory_bytes": 8, "tool_call_count": 1},
        artifacts={"result.json": canonical_bytes({"arm": invocation["arm"]})},
        started_at=NOW, completed_at=NOW,
    )


class Adapter:
    def __init__(self, *, fail_baseline=False, raise_first=False):
        self.calls, self.workspaces = [], []
        self.fail_baseline, self.raise_first = fail_baseline, raise_first

    def execute(self, invocation: Mapping[str, object], workspace: Path):
        assert not list(workspace.iterdir())
        self.calls.append(str(invocation["invocation_id"])); self.workspaces.append(workspace)
        (workspace / "residue").write_text("destroy", encoding="utf-8")
        if self.raise_first and len(self.calls) == 1:
            raise ConnectionError("secret provider message")
        state = "task_failure" if self.fail_baseline and invocation["arm"] == "baseline" else "succeeded"
        return capture(invocation, state)


def signed():
    key = Ed25519PrivateKey.generate()
    envelope = sign_plan(generate_plan(spec()), private_key=key, issuer_key_id="plan-key")
    return envelope, key


def build(tmp_path: Path, adapter=None, nonce_fn=None):
    envelope, plan_key = signed(); bundle_key = Ed25519PrivateKey.generate()
    selected = adapter or Adapter(); extra = {} if nonce_fn is None else {"nonce_fn": nonce_fn}
    bundle = run_experiment(
        envelope, trusted_plan_keys={"plan-key": pub(plan_key)}, adapter=selected,
        output_dir=tmp_path / "bundles", bundle_private_key=bundle_key,
        bundle_issuer_key_id="bundle-key", now_fn=lambda: NOW, **extra,
    )
    return bundle, selected, envelope, plan_key, bundle_key


def load(path: Path): return json.loads(path.read_text(encoding="utf-8"))
def write(path: Path, value): path.write_text(json.dumps(value, indent=2, sort_keys=True)+"\n", encoding="utf-8")
def replay(bundle, plan_key, bundle_key):
    return replay_bundle(bundle, trusted_plan_keys={"plan-key": pub(plan_key)},
                         trusted_bundle_keys={"bundle-key": pub(bundle_key)})


def test_plan_is_complete_paired_randomized_and_forward_only() -> None:
    plan = generate_plan(spec()); assert plan == generate_plan(spec())
    assert plan["planned_invocation_count"] == 12 and len(plan["blocks"]) == 6
    for block in plan["blocks"]:
        rows = [r for r in plan["invocations"] if r["invocation_id"] in block["invocation_ids"]]
        assert {r["arm"] for r in rows} == {"baseline", "candidate"}
        assert len({r["pairing_key"] for r in rows}) == len({r["agent_seed"] for r in rows}) == 1
    assert generate_plan(spec(placebo=True))["planned_invocation_count"] == 18
    for mutate in (lambda x: x.update(repetitions=2), lambda x: x["tasks"].reverse()):
        bad = spec(); mutate(bad)
        with pytest.raises(ExperimentError): generate_plan(bad)

    envelope, key = signed(); verify_plan_envelope(envelope, {"plan-key": pub(key)})
    changed = copy.deepcopy(envelope); changed["payload"]["blocks"][0]["arm_order"].reverse()
    raw = canonical_bytes(changed["payload"]); changed["plan_hash"] = sha256_bytes(raw)
    changed["signature"] = base64.b64encode(key.sign(PLAN_SIGNATURE_DOMAIN + raw)).decode()
    with pytest.raises(ExperimentError, match="preregistration"):
        verify_plan_envelope(changed, {"plan-key": pub(key)})


def test_version_two_plan_binds_artifact_baseline_and_protocol() -> None:
    key = Ed25519PrivateKey.generate()
    plan = generate_plan(version_two_spec())

    assert plan["schema_version"] == "arena-experiment-plan@2"
    assert plan["spec"]["study_protocol_digest"] == sha256_bytes(
        b"frozen-study-protocol"
    )
    assert {
        row["skill_artifact_digest"]
        for row in plan["invocations"]
        if row["arm"] == "baseline"
    } == {sha256_bytes(b"baseline-skill")}
    envelope = sign_plan(plan, private_key=key, issuer_key_id="v2-plan-key")
    assert envelope["schema_version"] == "arena-experiment-plan-envelope@2"
    assert verify_plan_envelope(envelope, {"v2-plan-key": pub(key)}) == plan

    changed = version_two_spec()
    changed["baseline_skill_artifact_digest"] = changed[
        "candidate_skill_artifact_digest"
    ]
    with pytest.raises(ExperimentError, match="digests must differ"):
        generate_plan(changed)

    placebo = version_two_spec()
    placebo["placebo_skill_artifact_digest"] = sha256_bytes(b"placebo")
    with pytest.raises(ExperimentError, match="do not support placebo"):
        generate_plan(placebo)


def test_runner_preserves_denominator_failures_and_fresh_workspaces(tmp_path: Path) -> None:
    adapter = Adapter(fail_baseline=True)
    bundle, _, envelope, plan_key, bundle_key = build(tmp_path, adapter)
    planned = envelope["payload"]["planned_invocation_count"]
    assert len(adapter.calls) == len(set(adapter.calls)) == planned
    assert all(not path.exists() for path in adapter.workspaces)
    result = replay(bundle, plan_key, bundle_key)
    assert result["recorded_invocation_count"] == planned
    assert result["outcome_counts"]["task_failure"] == 6


def test_adapter_exception_is_visible_but_secret_message_is_scrubbed(tmp_path: Path) -> None:
    bundle, _, _, plan_key, bundle_key = build(tmp_path, Adapter(raise_first=True))
    assert replay(bundle, plan_key, bundle_key)["outcome_counts"]["infrastructure_failure"] == 1
    first = next((bundle / "invocations").iterdir())
    assert b"secret provider message" not in (first / "stderr.bin").read_bytes()
    with pytest.raises(ExperimentError, match="reused"):
        build(tmp_path / "dup", nonce_fn=lambda: "same-nonce")


@pytest.mark.parametrize("kind", ["trajectory", "unknown", "unsafe"])
def test_runner_rejects_bad_adapter_capture(tmp_path: Path, kind: str) -> None:
    class Bad:
        def execute(self, invocation, workspace):
            value = capture(invocation)
            patch = ({"trajectory": {}} if kind == "trajectory" else
                     {"classification": "mystery"} if kind == "unknown" else
                     {"artifacts": {"../escape": b"x"}})
            return InvocationCapture(**{**value.__dict__, **patch})
    envelope, key = signed()
    with pytest.raises(ExperimentError):
        run_experiment(envelope, trusted_plan_keys={"plan-key": pub(key)}, adapter=Bad(),
                       output_dir=tmp_path, bundle_private_key=Ed25519PrivateKey.generate(),
                       bundle_issuer_key_id="bundle-key", now_fn=lambda: NOW)


@pytest.mark.parametrize("kind", ["output", "trace", "extra", "invocation", "order", "signature"])
def test_offline_replay_rejects_tamper_and_missing_evidence(tmp_path: Path, kind: str) -> None:
    bundle, _, _, plan_key, bundle_key = build(tmp_path)
    parent = tmp_path / "copy"; parent.mkdir(); changed = parent / bundle.name
    shutil.copytree(bundle, changed); first = next((changed / "invocations").iterdir())
    if kind == "output": (first / "artifacts/result.json").write_bytes(b"tampered")
    elif kind == "trace": (first / "trajectory.json").unlink()
    elif kind == "extra": (first / "extra.txt").write_text("x", encoding="utf-8")
    elif kind == "invocation": shutil.rmtree(first)
    elif kind == "order":
        index = load(changed / "run-index.json"); index["execution_sequence"].reverse()
        index["run_index_digest"] = sha256_json({k:v for k,v in index.items() if k != "run_index_digest"})
        write(changed / "run-index.json", index)
    else:
        envelope = load(changed / "bundle-envelope.json")
        envelope["signature"] = base64.b64encode(b"x"*64).decode()
        write(changed / "bundle-envelope.json", envelope)
    with pytest.raises(ExperimentError): replay(changed, plan_key, bundle_key)


def test_bundle_is_content_addressed_and_schema_covers_emitted_documents(tmp_path: Path) -> None:
    bundle, _, envelope, plan_key, bundle_key = build(tmp_path)
    signed_bundle = load(bundle / "bundle-envelope.json")
    assert bundle.name == "sha256-" + signed_bundle["manifest_hash"].removeprefix("sha256:")
    assert replay(bundle, plan_key, bundle_key)["status"] == "verified"
    schema = load(Path(__file__).resolve().parents[1] / "contracts/arena-experiment.schema.json")
    validator = Draft202012Validator(schema); first = next((bundle / "invocations").iterdir())
    documents = [spec(), envelope["payload"], envelope, load(bundle/"run-index.json"),
                 load(bundle/"bundle-manifest.json"), signed_bundle,
                 *[load(first/name) for name in ("invocation.json", "outcome.json", "metrics.json",
                                                  "trajectory.json", "verifier.json", "invocation-manifest.json")]]
    for document in documents:
        assert list(validator.iter_errors(document)) == []
