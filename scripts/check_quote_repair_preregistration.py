#!/usr/bin/env python3
"""Verify the quote-repair preregistration without private key or provider access."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.experiment import verify_plan_envelope  # noqa: E402
from skill_arena.experiment.model import (  # noqa: E402
    ExperimentError,
    is_sha256,
    require_sha256,
    sha256_bytes,
    sha256_json,
)
from skill_arena.experiment.provider_policy import load_provider_policy  # noqa: E402
from skill_arena.experiment.quote_repair import (  # noqa: E402
    load_quote_repair_protocol,
    load_quote_repair_task_bundle,
)


def _object(path: Path, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ExperimentError(f"{label} is absent or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise ExperimentError(f"{label} root is invalid")
    return value


def _manifest_at(commit: str, path: str) -> dict[str, object]:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{path}"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ExperimentError("artifact manifest is unreachable from declared commit")
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ExperimentError("artifact manifest at commit is invalid") from exc
    if not isinstance(value, dict):
        raise ExperimentError("artifact manifest at commit root is invalid")
    return value


def _verify_artifact(name: str, artifact: dict[str, object]) -> None:
    commit = str(artifact["commit"])
    source = _manifest_at(commit, "skills/repo_wiki_verified/manifest.json")
    portable = _manifest_at(
        commit, "dist/agent-skills/repo-wiki-verified/export-manifest.json"
    )
    if source.get("artifact_digest") != artifact["source_artifact_digest"]:
        raise ExperimentError(f"{name} source artifact digest mismatch")
    portable_value = portable.get("portable")
    if not isinstance(portable_value, dict) or portable_value.get(
        "artifact_digest"
    ) != artifact["portable_artifact_digest"]:
        raise ExperimentError(f"{name} portable artifact digest mismatch")


def _first_parent(commit: str) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", f"{commit}^1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise ExperimentError("candidate first parent is unreachable")
    return completed.stdout.strip()


def _string_list(value: object, label: str) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ExperimentError(f"{label} must be a unique string list")
    return value


def _verify_environment_trust(receipt: dict[str, object]) -> None:
    if receipt.get("trust_registry_path") != (
        "data/arena/quote-repair-environment-trust.json"
    ):
        raise ExperimentError("quote-repair environment trust path is unsupported")
    trust = _object(
        ROOT / str(receipt["trust_registry_path"]),
        "quote-repair environment trust registry",
    )
    expected_fields = {
        "schema_version", "registry_id", "actor_id", "authority_id", "updated_at",
        "active_receipt_ids", "revoked_receipt_ids", "superseded_receipt_ids",
        "registry_digest",
    }
    if set(trust) != expected_fields or trust.get("schema_version") != (
        "quote-repair-environment-trust@1"
    ):
        raise ExperimentError("quote-repair environment trust fields are invalid")
    if trust.get("actor_id") != "github:ed3c" or trust.get(
        "authority_id"
    ) != "repository-owner-review@1":
        raise ExperimentError("quote-repair environment trust authority is invalid")
    if trust.get("registry_id") != "quote-repair-environment-trust@1":
        raise ExperimentError("quote-repair environment trust registry id is invalid")
    active = _string_list(
        trust.get("active_receipt_ids"), "quote-repair active receipt ids"
    )
    revoked = _string_list(
        trust.get("revoked_receipt_ids"), "quote-repair revoked receipt ids"
    )
    superseded = _string_list(
        trust.get("superseded_receipt_ids"), "quote-repair superseded receipt ids"
    )
    if set(active) & (set(revoked) | set(superseded)) or set(revoked) & set(superseded):
        raise ExperimentError("quote-repair environment trust states overlap")
    stored = require_sha256(
        trust.get("registry_digest"), "quote-repair environment trust registry"
    )
    without_digest = {key: value for key, value in trust.items() if key != "registry_digest"}
    if stored != sha256_json(without_digest):
        raise ExperimentError("quote-repair environment trust digest mismatch")
    receipt_id = receipt.get("receipt_id")
    if receipt_id in revoked:
        raise ExperimentError("quote-repair environment receipt is revoked")
    if receipt_id in superseded:
        raise ExperimentError("quote-repair environment receipt is superseded")
    if receipt_id not in active:
        raise ExperimentError("quote-repair environment receipt is not active")
    if receipt.get("trust_registry_digest_at_observation") != stored:
        raise ExperimentError("quote-repair environment observation trust mismatch")


def _verify_environment(path: Path, protocol: dict[str, object]) -> None:
    receipt = _object(path, "quote-repair environment receipt")
    expected_fields = {
        "schema_version", "receipt_id", "environment_id", "dockerfile_digest", "verifier_digest",
        "base_image", "image_digest", "platform", "docker_server_version",
        "built_at", "network_policy", "cleanup_proof", "observer_actor_id",
        "observation_authority_id", "trust_registry_path",
        "trust_registry_digest_at_observation", "receipt_digest",
    }
    if set(receipt) != expected_fields or receipt.get("schema_version") != (
        "quote-repair-environment-receipt@1"
    ):
        raise ExperimentError("quote-repair environment receipt fields are invalid")
    if receipt.get("observer_actor_id") != "github:ed3c" or receipt.get(
        "observation_authority_id"
    ) != "repository-owner-review@1":
        raise ExperimentError("quote-repair environment observation authority is invalid")
    _verify_environment_trust(receipt)
    for field in ("dockerfile_digest", "verifier_digest", "image_digest", "receipt_digest"):
        require_sha256(receipt.get(field), f"quote-repair environment {field}")
    files = {
        "dockerfile_digest": ROOT / "data/arena/quote-repair-environment/Dockerfile",
        "verifier_digest": ROOT / "data/arena/quote-repair-environment/verifier.py",
    }
    for field, source in files.items():
        if source.is_symlink() or not source.is_file() or receipt[field] != sha256_bytes(
            source.read_bytes()
        ):
            raise ExperimentError(f"quote-repair environment {field} mismatch")
    if receipt.get("image_digest") != protocol["environment_image_digest"]:
        raise ExperimentError("quote-repair environment image digest mismatch")
    without_digest = {
        key: value for key, value in receipt.items() if key != "receipt_digest"
    }
    if receipt["receipt_digest"] != sha256_json(without_digest) or receipt[
        "receipt_digest"
    ] != protocol["environment_receipt_digest"]:
        raise ExperimentError("quote-repair environment receipt digest mismatch")


def _current_trust(public_document: dict[str, object]) -> None:
    if public_document.get("trust_registry_path") != (
        "data/arena/quote-repair-plan-trust.json"
    ):
        raise ExperimentError("quote-repair trust registry path is unsupported")
    trust = _object(ROOT / str(public_document["trust_registry_path"]), "quote-repair trust registry")
    expected_fields = {
        "schema_version", "registry_id", "actor_id", "authority_id", "updated_at",
        "active_keys", "revoked_key_ids", "superseded_key_ids", "registry_digest",
    }
    if set(trust) != expected_fields or trust.get("schema_version") != "quote-repair-plan-trust@1":
        raise ExperimentError("quote-repair trust registry fields are invalid")
    if trust.get("actor_id") != "github:ed3c" or trust.get(
        "authority_id"
    ) != "repository-owner-review@1":
        raise ExperimentError("quote-repair plan trust authority is invalid")
    if trust.get("registry_id") != "quote-repair-plan-trust@1":
        raise ExperimentError("quote-repair plan trust registry id is invalid")
    revoked = _string_list(trust.get("revoked_key_ids"), "quote-repair revoked key ids")
    superseded = _string_list(
        trust.get("superseded_key_ids"), "quote-repair superseded key ids"
    )
    stored = require_sha256(trust.get("registry_digest"), "quote-repair trust registry")
    without_digest = {key: value for key, value in trust.items() if key != "registry_digest"}
    if stored != sha256_json(without_digest):
        raise ExperimentError("quote-repair trust registry digest mismatch")
    if public_document.get("trust_registry_digest_at_issue") != stored:
        raise ExperimentError("quote-repair mint-time trust registry mismatch")
    active = trust.get("active_keys")
    if (
        not isinstance(active, dict)
        or any(
            not isinstance(active_key, str)
            or not active_key
            or not is_sha256(active_digest)
            for active_key, active_digest in active.items()
        )
    ):
        raise ExperimentError("quote-repair active keys are invalid")
    if set(active) & (set(revoked) | set(superseded)) or set(revoked) & set(superseded):
        raise ExperimentError("quote-repair plan trust states overlap")
    key_id = public_document.get("key_id")
    if key_id in revoked:
        raise ExperimentError("quote-repair plan key is revoked")
    if key_id in superseded:
        raise ExperimentError("quote-repair plan key is superseded")
    if active.get(key_id) != public_document.get("public_key_digest"):
        raise ExperimentError("quote-repair plan key is not active")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--task-bundle", type=Path, required=True)
    result.add_argument("--protocol", type=Path, required=True)
    result.add_argument("--provider-policy", type=Path, required=True)
    result.add_argument("--environment-receipt", type=Path, required=True)
    result.add_argument("--plan", type=Path, required=True)
    result.add_argument("--public-key", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        bundle = load_quote_repair_task_bundle(args.task_bundle)
        protocol = load_quote_repair_protocol(args.protocol, bundle)
        policy = load_provider_policy(args.provider_policy)
        if protocol["provider_policy_digest"] != policy.digest:
            raise ExperimentError("quote-repair provider policy digest mismatch")
        _verify_environment(args.environment_receipt, protocol)
        for name in ("baseline", "candidate"):
            _verify_artifact(name, protocol[f"{name}_artifact"])
        if _first_parent(str(protocol["candidate_artifact"]["commit"])) != protocol[
            "baseline_artifact"
        ]["commit"]:
            raise ExperimentError("quote-repair baseline is not candidate first parent")
        public_document = _object(args.public_key, "quote-repair public key")
        expected_fields = {
            "schema_version",
            "key_id",
            "purpose",
            "algorithm",
            "public_key_base64",
            "public_key_digest",
            "issued_at",
            "actor_id",
            "authority_id",
            "trust_registry_path",
            "trust_registry_digest_at_issue",
            "plan_hash",
            "plan_digest",
            "execution_authorized",
            "document_digest",
        }
        if set(public_document) != expected_fields:
            raise ExperimentError("quote-repair public key fields are invalid")
        if public_document.get("purpose") != "experiment-preregistration" or public_document.get(
            "algorithm"
        ) != "ed25519":
            raise ExperimentError("quote-repair public key purpose is invalid")
        if public_document.get("execution_authorized") is not False:
            raise ExperimentError("quote-repair execution authority is invalid")
        if public_document.get("actor_id") != "github:ed3c" or public_document.get(
            "authority_id"
        ) != "repository-owner-review@1":
            raise ExperimentError("quote-repair plan key authority is invalid")
        require_sha256(
            public_document.get("trust_registry_digest_at_issue"),
            "quote-repair mint-time trust registry",
        )
        _current_trust(public_document)
        try:
            public = base64.b64decode(
                str(public_document["public_key_base64"]), validate=True
            )
        except (ValueError, TypeError, binascii.Error) as exc:
            raise ExperimentError("quote-repair public key encoding is invalid") from exc
        if len(public) != 32 or public_document.get("public_key_digest") != sha256_bytes(
            public
        ):
            raise ExperimentError("quote-repair public key digest mismatch")
        document_digest = require_sha256(
            public_document.get("document_digest"), "quote-repair key document"
        )
        without_digest = {
            key: value
            for key, value in public_document.items()
            if key != "document_digest"
        }
        if document_digest != sha256_json(without_digest):
            raise ExperimentError("quote-repair key document digest mismatch")
        envelope = _object(args.plan, "quote-repair plan")
        plan = verify_plan_envelope(
            envelope, {str(public_document["key_id"]): public}
        )
        expected_spec = {
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
            "study_protocol_digest": protocol["protocol_digest"],
            "environment_image_digest": protocol["environment_image_digest"],
            "policy_digest": protocol["provider_policy_digest"],
            "allowed_tools_digest": protocol["allowed_tools_digest"],
            "repetitions": 5,
        }
        if any(plan["spec"].get(key) != value for key, value in expected_spec.items()):
            raise ExperimentError("quote-repair signed plan binding mismatch")
        if plan.get("planned_invocation_count") != len(bundle["tasks"]) * 5 * 2:
            raise ExperimentError("quote-repair invocation denominator mismatch")
        if public_document.get("plan_hash") != envelope.get(
            "plan_hash"
        ) or public_document.get("plan_digest") != plan.get("plan_digest"):
            raise ExperimentError("quote-repair public key plan binding mismatch")
        print(
            "PASS: quote-repair preregistration verified"
            f" invocations={plan['planned_invocation_count']}"
            f" plan_digest={plan['plan_digest']}"
            " execution_authorized=false"
        )
        return 0
    except ExperimentError as exc:
        print(f"FAIL: quote-repair preregistration: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
