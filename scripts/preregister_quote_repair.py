#!/usr/bin/env python3
"""Generate the signed, execution-disabled quote-repair preregistration."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.experiment import generate_plan, sign_plan  # noqa: E402
from skill_arena.experiment.model import (  # noqa: E402
    ExperimentError,
    SPEC_SCHEMA_V2,
    sha256_bytes,
    sha256_json,
)
from skill_arena.experiment.quote_repair import (  # noqa: E402
    load_quote_repair_protocol,
    load_quote_repair_task_bundle,
)


def _private_key(path: Path) -> Ed25519PrivateKey:
    if path.is_symlink() or not path.is_file():
        raise ExperimentError("preregistration private key is absent or unsafe")
    try:
        data = path.read_bytes()
        key = serialization.load_pem_private_key(data, password=None)
    except (OSError, TypeError, ValueError) as exc:
        raise ExperimentError("preregistration private key is invalid") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ExperimentError("preregistration private key is not Ed25519")
    return key


def _write_exclusive(path: Path, value: object) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ExperimentError("preregistration output parent is absent or unsafe")
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    except FileExistsError as exc:
        raise ExperimentError("preregistration output already exists") from exc
    except OSError as exc:
        raise ExperimentError("cannot write preregistration output") from exc


def _trust_registry(path: Path, key_id: str, public_digest: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError("cannot read preregistration trust registry") from exc
    if not isinstance(value, dict):
        raise ExperimentError("preregistration trust registry root is invalid")
    if value.get("active_keys") != {key_id: public_digest}:
        raise ExperimentError("preregistration key is not active in trust registry")
    stored = value.get("registry_digest")
    without_digest = {key: item for key, item in value.items() if key != "registry_digest"}
    if stored != sha256_json(without_digest):
        raise ExperimentError("preregistration trust registry digest mismatch")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--task-bundle", type=Path, required=True)
    result.add_argument("--protocol", type=Path, required=True)
    result.add_argument("--private-key", type=Path, required=True)
    result.add_argument("--issuer-key-id", required=True)
    result.add_argument("--trust-registry", type=Path, required=True)
    result.add_argument("--plan-output", type=Path, required=True)
    result.add_argument("--public-key-output", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        bundle = load_quote_repair_task_bundle(args.task_bundle)
        protocol = load_quote_repair_protocol(args.protocol, bundle)
        key = _private_key(args.private_key)
        spec = {
            "schema_version": SPEC_SCHEMA_V2,
            "experiment_id": "quote-repair-diagnostic-efficacy-v1",
            "tasks": [
                {
                    "task_id": task["task_id"],
                    "task_family": task["task_family"],
                    "task_bundle_digest": task["task_digest"],
                }
                for task in bundle["tasks"]
            ],
            "baseline_skill_artifact_digest": protocol["baseline_artifact"][
                "portable_artifact_digest"
            ],
            "candidate_skill_artifact_digest": protocol["candidate_artifact"][
                "portable_artifact_digest"
            ],
            "placebo_skill_artifact_digest": None,
            "study_protocol_digest": protocol["protocol_digest"],
            "agent_id": "pi-acp",
            "model_id": "vllm/qwen3:4b",
            "harness_id": "benchflow-quote-repair",
            "harness_version": "0.6.3",
            "sandbox_profile_id": "local-docker-arm64@1",
            "environment_image_digest": protocol["environment_image_digest"],
            "policy_digest": protocol["provider_policy_digest"],
            "network_policy": "allowlisted",
            "allowed_tools_digest": protocol["allowed_tools_digest"],
            "repetitions": protocol["repetitions"],
            "randomization_seed": 530053,
            "agent_seed_mode": "unavailable",
            "preregistered_at": protocol["preregistered_at"],
        }
        envelope = sign_plan(
            generate_plan(spec),
            private_key=key,
            issuer_key_id=args.issuer_key_id,
        )
        public = key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        public_digest = sha256_bytes(public)
        trust = _trust_registry(args.trust_registry, args.issuer_key_id, public_digest)
        public_without_digest = {
            "schema_version": "quote-repair-plan-public-key@1",
            "key_id": args.issuer_key_id,
            "purpose": "experiment-preregistration",
            "algorithm": "ed25519",
            "public_key_base64": base64.b64encode(public).decode("ascii"),
            "public_key_digest": public_digest,
            "issued_at": protocol["preregistered_at"],
            "actor_id": "github:ed3c",
            "authority_id": "repository-owner-review@1",
            "trust_registry_path": "data/arena/quote-repair-plan-trust.json",
            "trust_registry_digest_at_issue": trust["registry_digest"],
            "plan_hash": envelope["plan_hash"],
            "plan_digest": envelope["payload"]["plan_digest"],
            "execution_authorized": False,
        }
        public_document = {
            **public_without_digest,
            "document_digest": sha256_json(public_without_digest),
        }
        _write_exclusive(args.plan_output, envelope)
        _write_exclusive(args.public_key_output, public_document)
        print(
            "PASS: signed quote-repair preregistration"
            f" invocations={envelope['payload']['planned_invocation_count']}"
            f" plan_hash={envelope['plan_hash']}"
            " execution_authorized=false"
        )
        return 0
    except ExperimentError as exc:
        print(f"FAIL: quote-repair preregistration: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
