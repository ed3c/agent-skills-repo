#!/usr/bin/env python3
"""Execute one real paired Arena matrix through BenchFlow and GitHub Models."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.experiment import (  # noqa: E402
    BenchFlowExperimentAdapter,
    ExperimentError,
    enforce_github_models_retirement,
    fetch_github_model_catalog_evidence,
    generate_plan,
    load_runtime_policy,
    prepare_benchflow_runtime,
    replay_bundle,
    run_experiment,
    sign_plan,
    summarize_paired_bundle,
)
from skill_arena.experiment.model import (  # noqa: E402
    SPEC_SCHEMA,
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


def _raw_public(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def _find_bundle(output_root: Path, task_id: str) -> Path:
    index = _load_object(output_root / "index.json", "SkillsBench bundle index")
    rows = index.get("bundles")
    if not isinstance(rows, list):
        raise ExperimentError("SkillsBench bundle index has no bundles")
    matches = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("task_id") == task_id
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("bundle_path"), str):
        raise ExperimentError(f"SkillsBench bundle index must contain one {task_id!r}")
    path = output_root / str(matches[0]["bundle_path"])
    if path.is_symlink() or not path.is_dir():
        raise ExperimentError("selected SkillsBench bundle path is absent or unsafe")
    return path


def _randomization_seed(run_identity: str) -> int:
    raw = hashlib.sha256(run_identity.encode("utf-8")).digest()
    return int.from_bytes(raw[:8], "big") & ((1 << 63) - 1)


def _public_keys_document(
    plan_key: Ed25519PrivateKey,
    bundle_key: Ed25519PrivateKey,
) -> dict[str, object]:
    return {
        "schema_version": "arena-experiment-public-keys@1",
        "keys": [
            {
                "key_id": "github-actions-plan",
                "purpose": "experiment-preregistration",
                "algorithm": "Ed25519",
                "public_key_base64": base64.b64encode(_raw_public(plan_key)).decode(),
            },
            {
                "key_id": "github-actions-bundle",
                "purpose": "experiment-bundle",
                "algorithm": "Ed25519",
                "public_key_base64": base64.b64encode(_raw_public(bundle_key)).decode(),
            },
        ],
    }


def _artifact_manifest(root: Path) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == "artifact-manifest.json":
            continue
        if path.is_symlink():
            raise ExperimentError(f"runtime artifact contains symlink: {relative}")
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ExperimentError(f"runtime artifact contains special file: {relative}")
        data = path.read_bytes()
        files.append(
            {
                "path": relative,
                "sha256": sha256_bytes(data),
                "size_bytes": len(data),
            }
        )
    value_without_digest = {
        "schema_version": "arena-runtime-artifact-manifest@1",
        "files": files,
    }
    return {
        **value_without_digest,
        "manifest_digest": "sha256:"
        + hashlib.sha256(canonical_bytes(value_without_digest)).hexdigest(),
    }


def command_run(args: argparse.Namespace) -> int:
    policy = load_runtime_policy(args.runtime_policy)
    retirement_authority = _load_object(
        args.retirement_authority, "GitHub Models retirement authority"
    )
    if args.task_id != policy.task_id:
        raise ExperimentError("requested task differs from runtime policy")
    started_at = datetime.now(timezone.utc)
    enforce_github_models_retirement(
        policy=policy,
        checked_at=started_at,
        retirement_authority=retirement_authority,
    )
    token = os.environ.get(args.github_token_env, "")
    if not token:
        raise ExperimentError(
            f"workflow token environment variable is absent: {args.github_token_env}"
        )
    bench_bin = args.bench_bin.resolve(strict=True)
    bundles_root = args.bundles_root.resolve(strict=True)
    bundle_dir = _find_bundle(bundles_root, policy.task_id)
    destination = args.output_root
    if destination.exists():
        if destination.is_symlink() or not destination.is_dir():
            raise ExperimentError("runtime output root is not a directory")
        if any(destination.iterdir()):
            raise ExperimentError("runtime output root must start empty")
    destination.mkdir(parents=True, exist_ok=True)

    catalog = fetch_github_model_catalog_evidence(
        token=token,
        policy=policy,
        retirement_authority=retirement_authority,
    )
    _write_object(destination / "model-catalog-evidence.json", catalog)
    shutil.copyfile(args.runtime_policy, destination / "runtime-policy.json")
    preparation = prepare_benchflow_runtime(
        bundle_dir=bundle_dir,
        policy=policy,
        catalog_evidence=catalog,
        image_tag=f"arena-prepare-{args.run_identity}",
    )
    _write_object(destination / "preparation.json", preparation)

    plan_key = Ed25519PrivateKey.generate()
    bundle_key = Ed25519PrivateKey.generate()
    public_keys = _public_keys_document(plan_key, bundle_key)
    _write_object(destination / "public-keys.json", public_keys)
    preregistered_at = datetime.now(timezone.utc)
    spec = {
        "schema_version": SPEC_SCHEMA,
        "experiment_id": f"skillsbench-{policy.task_id}-{args.run_identity}",
        "tasks": [
            {
                "task_id": policy.task_id,
                "task_family": policy.task_family,
                "task_bundle_digest": policy.task_bundle_digest,
            }
        ],
        "candidate_skill_artifact_digest": preparation[
            "candidate_skill_artifact_digest"
        ],
        "placebo_skill_artifact_digest": None,
        "agent_id": policy.agent,
        "model_id": policy.model,
        "harness_id": "benchflow",
        "harness_version": policy.benchflow_version,
        "sandbox_profile_id": policy.sandbox_profile_id,
        "environment_image_digest": preparation["environment_image_digest"],
        "policy_digest": preparation["effective_policy_digest"],
        "network_policy": policy.network_policy,
        "allowed_tools_digest": policy.allowed_tools_digest,
        "repetitions": policy.repetitions,
        "randomization_seed": _randomization_seed(args.run_identity),
        "agent_seed_mode": "unavailable",
        "preregistered_at": preregistered_at.isoformat().replace("+00:00", "Z"),
    }
    plan_envelope = sign_plan(
        generate_plan(spec),
        private_key=plan_key,
        issuer_key_id="github-actions-plan",
    )
    _write_object(destination / "preregistered-plan-envelope.json", plan_envelope)

    adapter = BenchFlowExperimentAdapter(
        bench_bin=bench_bin,
        bundle_dir=bundle_dir,
        policy=policy,
        catalog_evidence=catalog,
        preparation=preparation,
        github_token=token,
        image_tag_prefix=f"arena-{args.run_identity}",
    )
    bundle = run_experiment(
        plan_envelope,
        trusted_plan_keys={"github-actions-plan": _raw_public(plan_key)},
        adapter=adapter,
        output_dir=destination / "bundles",
        bundle_private_key=bundle_key,
        bundle_issuer_key_id="github-actions-bundle",
    )
    replay = replay_bundle(
        bundle,
        trusted_plan_keys={"github-actions-plan": _raw_public(plan_key)},
        trusted_bundle_keys={"github-actions-bundle": _raw_public(bundle_key)},
    )
    paired = summarize_paired_bundle(bundle)
    _write_object(destination / "replay-result.json", replay)
    _write_object(destination / "paired-result.json", paired)
    completed_at = datetime.now(timezone.utc)
    summary = {
        "schema_version": "arena-real-runtime-summary@1",
        "status": "complete",
        "run_identity": args.run_identity,
        "task_id": policy.task_id,
        "agent": policy.agent,
        "model": policy.model,
        "model_catalog_version": catalog["version"],
        "bundle_path": bundle.relative_to(destination).as_posix(),
        "bundle_manifest_hash": replay["manifest_hash"],
        "plan_digest": replay["plan_digest"],
        "preparation_digest": preparation["preparation_digest"],
        "paired_result_digest": paired["result_digest"],
        "started_at": started_at.isoformat().replace("+00:00", "Z"),
        "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
        "ranking_claim_allowed": False,
    }
    _write_object(destination / "runtime-summary.json", summary)
    _write_object(destination / "artifact-manifest.json", _artifact_manifest(destination))

    arms = paired["arms"]
    assert isinstance(arms, dict)
    incomplete = [
        arm
        for arm in ("baseline", "candidate")
        if not isinstance(arms.get(arm), dict)
        or arms[arm].get("scored") != arms[arm].get("planned")
    ]
    if incomplete:
        print(
            "FAIL: real paired matrix retained unscored invocations: "
            + ",".join(incomplete),
            file=sys.stderr,
        )
        return 3
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--runtime-policy", type=Path, required=True)
    root.add_argument("--retirement-authority", type=Path, required=True)
    root.add_argument("--bundles-root", type=Path, required=True)
    root.add_argument("--task-id", required=True)
    root.add_argument("--bench-bin", type=Path, required=True)
    root.add_argument("--output-root", type=Path, required=True)
    root.add_argument("--run-identity", required=True)
    root.add_argument("--github-token-env", default="GITHUB_TOKEN")
    root.set_defaults(handler=command_run)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except ExperimentError as exc:
        print(f"FAIL: Arena BenchFlow runtime: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
