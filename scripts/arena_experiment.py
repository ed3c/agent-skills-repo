#!/usr/bin/env python3
"""Create signed Arena plans and replay signed experiment bundles offline."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.experiment import (  # noqa: E402
    ExperimentError,
    InvocationCapture,
    generate_plan,
    replay_bundle,
    run_experiment,
    sign_plan,
    verify_plan_envelope,
)
from skill_arena.experiment.model import (  # noqa: E402
    METRICS_SCHEMA,
    SPEC_SCHEMA,
    TRAJECTORY_SCHEMA,
    VERIFIER_SCHEMA,
    canonical_bytes,
    sha256_bytes,
)


def _load_object(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExperimentError(f"{label} root must be an object")
    return value


def _write_object(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ExperimentError(f"cannot read private key: {path}: {exc}") from exc
    try:
        if data.startswith(b"-----BEGIN"):
            key = serialization.load_pem_private_key(data, password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ExperimentError("private key is not Ed25519")
            return key
        if len(data) != 32:
            raise ExperimentError("raw Ed25519 private key must contain 32 bytes")
        return Ed25519PrivateKey.from_private_bytes(data)
    except (TypeError, ValueError) as exc:
        raise ExperimentError("private key is not a valid Ed25519 key") from exc


def _load_public_key(path: Path) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ExperimentError(f"cannot read public key: {path}: {exc}") from exc
    try:
        if data.startswith(b"-----BEGIN"):
            key = serialization.load_pem_public_key(data)
            if not isinstance(key, Ed25519PublicKey):
                raise ExperimentError("public key is not Ed25519")
            return key.public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        if len(data) != 32:
            raise ExperimentError("raw Ed25519 public key must contain 32 bytes")
        Ed25519PublicKey.from_public_bytes(data)
        return data
    except (TypeError, ValueError) as exc:
        raise ExperimentError("public key is not a valid Ed25519 key") from exc


def command_plan(args: argparse.Namespace) -> int:
    spec = _load_object(args.spec, "experiment spec")
    envelope = sign_plan(
        generate_plan(spec),
        private_key=_load_private_key(args.private_key),
        issuer_key_id=args.issuer_key_id,
    )
    _write_object(args.output, envelope)
    print(
        "PASS: signed preregistered plan"
        f" invocations={envelope['payload']['planned_invocation_count']}"
        f" hash={envelope['plan_hash']}"
    )
    return 0


def command_verify_plan(args: argparse.Namespace) -> int:
    envelope = _load_object(args.envelope, "plan envelope")
    plan = verify_plan_envelope(
        envelope,
        {args.issuer_key_id: _load_public_key(args.public_key)},
    )
    print(
        "PASS: plan envelope verified"
        f" invocations={plan['planned_invocation_count']}"
        f" digest={plan['plan_digest']}"
    )
    return 0


def command_replay(args: argparse.Namespace) -> int:
    result = replay_bundle(
        args.bundle,
        trusted_plan_keys={
            args.plan_key_id: _load_public_key(args.plan_public_key)
        },
        trusted_bundle_keys={
            args.bundle_key_id: _load_public_key(args.bundle_public_key)
        },
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


class _SelftestAdapter:
    def execute(
        self,
        invocation: Mapping[str, object],
        workspace: Path,
    ) -> InvocationCapture:
        if any(workspace.iterdir()):
            raise RuntimeError("workspace was not fresh")
        marker = workspace / "marker.txt"
        marker.write_text(str(invocation["invocation_id"]), encoding="utf-8")
        now = datetime(2026, 8, 7, 0, 0, tzinfo=timezone.utc)
        reward = 1.0
        artifact = canonical_bytes(
            {
                "arm": invocation["arm"],
                "invocation_id": invocation["invocation_id"],
            }
        )
        return InvocationCapture(
            classification="succeeded",
            reward=reward,
            adapter_exit_code=0,
            error_code=None,
            stdout=b"selftest stdout\n",
            stderr=b"",
            trajectory={
                "schema_version": TRAJECTORY_SCHEMA,
                "events": [{"type": "selftest", "workspace": marker.name}],
            },
            verifier={
                "schema_version": VERIFIER_SCHEMA,
                "status": "passed",
                "reward": reward,
                "diagnostics_digest": sha256_bytes(b"selftest diagnostics"),
            },
            metrics={
                "schema_version": METRICS_SCHEMA,
                "end_to_end_latency_ms": 1,
                "verifier_latency_ms": 1,
                "input_tokens": 0,
                "output_tokens": 0,
                "tool_tokens": 0,
                "cost_microunits": 0,
                "cpu_time_ms": 1,
                "peak_memory_bytes": 1,
                "tool_call_count": 0,
            },
            artifacts={"result.json": artifact},
            started_at=now,
            completed_at=now,
        )


def _raw_public_key(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def command_selftest(_: argparse.Namespace) -> int:
    plan_key = Ed25519PrivateKey.generate()
    bundle_key = Ed25519PrivateKey.generate()
    spec = {
        "schema_version": SPEC_SCHEMA,
        "experiment_id": "selftest",
        "tasks": [
            {
                "task_id": "task-one",
                "task_family": "selftest",
                "task_bundle_digest": sha256_bytes(b"task"),
            }
        ],
        "candidate_skill_artifact_digest": sha256_bytes(b"candidate"),
        "placebo_skill_artifact_digest": None,
        "agent_id": "selftest-agent",
        "model_id": "selftest-model",
        "harness_id": "selftest-harness",
        "harness_version": "1.0.0",
        "sandbox_profile_id": "selftest-sandbox",
        "environment_image_digest": sha256_bytes(b"image"),
        "policy_digest": sha256_bytes(b"policy"),
        "network_policy": "no-network",
        "allowed_tools_digest": sha256_bytes(b"tools"),
        "repetitions": 3,
        "randomization_seed": 7,
        "agent_seed_mode": "deterministic",
        "preregistered_at": "2026-08-07T00:00:00Z",
    }
    envelope = sign_plan(
        generate_plan(spec),
        private_key=plan_key,
        issuer_key_id="selftest-plan",
    )
    with tempfile.TemporaryDirectory(prefix="arena-experiment-selftest-") as temp:
        root = Path(temp)
        bundle = run_experiment(
            envelope,
            trusted_plan_keys={"selftest-plan": _raw_public_key(plan_key)},
            adapter=_SelftestAdapter(),
            output_dir=root / "bundles",
            bundle_private_key=bundle_key,
            bundle_issuer_key_id="selftest-bundle",
            now_fn=lambda: datetime(2026, 8, 7, 0, 1, tzinfo=timezone.utc),
        )
        replay_bundle(
            bundle,
            trusted_plan_keys={"selftest-plan": _raw_public_key(plan_key)},
            trusted_bundle_keys={"selftest-bundle": _raw_public_key(bundle_key)},
        )
        tampered = root / "tampered"
        shutil.copytree(bundle, tampered)
        first = next((tampered / "invocations").iterdir())
        (first / "stdout.bin").write_bytes(b"tampered\n")
        try:
            replay_bundle(
                tampered,
                trusted_plan_keys={"selftest-plan": _raw_public_key(plan_key)},
                trusted_bundle_keys={"selftest-bundle": _raw_public_key(bundle_key)},
            )
        except ExperimentError:
            pass
        else:
            raise ExperimentError("selftest tamper control was not rejected")
    print("PASS: Arena experiment plan, execution, replay, and tamper selftest")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan", help="generate and sign one preregistered plan")
    plan.add_argument("--spec", type=Path, required=True)
    plan.add_argument("--private-key", type=Path, required=True)
    plan.add_argument("--issuer-key-id", required=True)
    plan.add_argument("--output", type=Path, required=True)
    plan.set_defaults(handler=command_plan)

    verify = commands.add_parser("verify-plan", help="verify a signed plan offline")
    verify.add_argument("--envelope", type=Path, required=True)
    verify.add_argument("--public-key", type=Path, required=True)
    verify.add_argument("--issuer-key-id", required=True)
    verify.set_defaults(handler=command_verify_plan)

    replay = commands.add_parser("replay", help="verify a signed run bundle offline")
    replay.add_argument("--bundle", type=Path, required=True)
    replay.add_argument("--plan-public-key", type=Path, required=True)
    replay.add_argument("--plan-key-id", required=True)
    replay.add_argument("--bundle-public-key", type=Path, required=True)
    replay.add_argument("--bundle-key-id", required=True)
    replay.set_defaults(handler=command_replay)

    selftest = commands.add_parser("selftest")
    selftest.set_defaults(handler=command_selftest)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except ExperimentError as exc:
        print(f"FAIL: Arena experiment: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
