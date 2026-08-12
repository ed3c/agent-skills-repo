"""Deterministic preregistration, paired arm ordering, and plan signatures."""
from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Mapping, Sequence, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .model import (
    FAILURE_DENOMINATOR_POLICY,
    INVOCATION_SCHEMA,
    NO_SKILL,
    PLAN_SCHEMA,
    PLAN_SCHEMA_V2,
    PLAN_SIGNATURE_DOMAIN,
    PLAN_SIGNATURE_DOMAIN_V2,
    RANDOMIZATION_ALGORITHM,
    REQUIRED_EVIDENCE_FILES,
    SIGNED_PLAN_SCHEMA,
    SIGNED_PLAN_SCHEMA_V2,
    SPEC_SCHEMA_V2,
    WORKSPACE_POLICY,
    ExperimentError,
    _PLAN_ENVELOPE_FIELDS,
    _PLAN_FIELDS,
    canonical_bytes,
    identifier,
    sha256_bytes,
    sha256_json,
    validate_spec,
)


def _block_seed(spec: Mapping[str, object], task_digest: str, repetition: int) -> int:
    raw = hashlib.sha256(
        canonical_bytes(
            {
                "algorithm": RANDOMIZATION_ALGORITHM,
                "experiment_id": spec["experiment_id"],
                "randomization_seed": spec["randomization_seed"],
                "task_bundle_digest": task_digest,
                "repetition": repetition,
            }
        )
    ).digest()
    return int.from_bytes(raw[:8], "big") & ((1 << 63) - 1)


def _arm_order(arms: Sequence[str], block_seed: int) -> list[str]:
    keyed: list[tuple[str, str]] = []
    for arm in arms:
        key = hashlib.sha256(
            canonical_bytes(
                {
                    "algorithm": RANDOMIZATION_ALGORITHM,
                    "block_seed": block_seed,
                    "arm": arm,
                }
            )
        ).hexdigest()
        keyed.append((key, arm))
    if len({key for key, _ in keyed}) != len(keyed):
        raise ExperimentError("arm randomization hash collision")
    return [arm for _, arm in sorted(keyed)]


def _pairing_key(spec: Mapping[str, object], task_digest: str, repetition: int) -> str:
    return sha256_json(
        {
            "task_bundle_digest": task_digest,
            "repetition": repetition,
            "agent_id": spec["agent_id"],
            "model_id": spec["model_id"],
            "harness_id": spec["harness_id"],
            "harness_version": spec["harness_version"],
            "sandbox_profile_id": spec["sandbox_profile_id"],
            "environment_image_digest": spec["environment_image_digest"],
            "policy_digest": spec["policy_digest"],
            "network_policy": spec["network_policy"],
            "allowed_tools_digest": spec["allowed_tools_digest"],
        }
    )


def generate_plan(spec: Mapping[str, object]) -> dict[str, object]:
    """Generate the complete run matrix before any arm is executed."""
    normalized = validate_spec(spec)
    arms = ["baseline", "candidate"]
    if normalized["placebo_skill_artifact_digest"] is not None:
        arms.append("placebo")

    blocks: list[dict[str, object]] = []
    invocations: list[dict[str, object]] = []
    tasks = cast(list[dict[str, str]], normalized["tasks"])
    repetitions = cast(int, normalized["repetitions"])
    for task in tasks:
        for repetition in range(1, repetitions + 1):
            seed = _block_seed(normalized, task["task_bundle_digest"], repetition)
            order = _arm_order(arms, seed)
            pairing_key = _pairing_key(
                normalized,
                task["task_bundle_digest"],
                repetition,
            )
            block_base = {
                "experiment_id": normalized["experiment_id"],
                "task_bundle_digest": task["task_bundle_digest"],
                "repetition": repetition,
                "block_seed": seed,
                "pairing_key": pairing_key,
            }
            block_id = "block-" + hashlib.sha256(
                canonical_bytes(block_base)
            ).hexdigest()[:24]
            invocation_ids: list[str] = []
            for order_index, arm in enumerate(order):
                if arm == "baseline":
                    artifact = (
                        normalized["baseline_skill_artifact_digest"]
                        if normalized["schema_version"] == SPEC_SCHEMA_V2
                        else NO_SKILL
                    )
                elif arm == "candidate":
                    artifact = normalized["candidate_skill_artifact_digest"]
                else:
                    artifact = normalized["placebo_skill_artifact_digest"]
                invocation_without_id = {
                    "schema_version": INVOCATION_SCHEMA,
                    "block_id": block_id,
                    "pairing_key": pairing_key,
                    "task_id": task["task_id"],
                    "task_family": task["task_family"],
                    "task_bundle_digest": task["task_bundle_digest"],
                    "arm": arm,
                    "skill_artifact_digest": artifact,
                    "agent_id": normalized["agent_id"],
                    "model_id": normalized["model_id"],
                    "harness_id": normalized["harness_id"],
                    "harness_version": normalized["harness_version"],
                    "sandbox_profile_id": normalized["sandbox_profile_id"],
                    "environment_image_digest": normalized[
                        "environment_image_digest"
                    ],
                    "policy_digest": normalized["policy_digest"],
                    "network_policy": normalized["network_policy"],
                    "allowed_tools_digest": normalized["allowed_tools_digest"],
                    "repetition": repetition,
                    "block_seed": seed,
                    # Pairing requires the same agent seed across arms in a block.
                    "agent_seed": (
                        seed
                        if normalized["agent_seed_mode"] == "deterministic"
                        else None
                    ),
                    "order_index": order_index,
                    "workspace_policy": WORKSPACE_POLICY,
                }
                invocation_id = "inv-" + hashlib.sha256(
                    canonical_bytes(invocation_without_id)
                ).hexdigest()[:32]
                invocation = {
                    **invocation_without_id,
                    "invocation_id": invocation_id,
                }
                invocations.append(invocation)
                invocation_ids.append(invocation_id)
            blocks.append(
                {
                    "block_id": block_id,
                    "pairing_key": pairing_key,
                    "task_id": task["task_id"],
                    "task_bundle_digest": task["task_bundle_digest"],
                    "repetition": repetition,
                    "block_seed": seed,
                    "agent_seed": (
                        seed
                        if normalized["agent_seed_mode"] == "deterministic"
                        else None
                    ),
                    "arm_order": order,
                    "invocation_ids": invocation_ids,
                }
            )

    plan_without_digest: dict[str, object] = {
        "schema_version": (
            PLAN_SCHEMA_V2
            if normalized["schema_version"] == SPEC_SCHEMA_V2
            else PLAN_SCHEMA
        ),
        "randomization_algorithm": RANDOMIZATION_ALGORITHM,
        "failure_denominator_policy": FAILURE_DENOMINATOR_POLICY,
        "workspace_policy": WORKSPACE_POLICY,
        "spec": normalized,
        "arms": arms,
        "blocks": blocks,
        "invocations": invocations,
        "planned_invocation_count": len(invocations),
        "required_evidence_files": list(REQUIRED_EVIDENCE_FILES),
    }
    return {
        **plan_without_digest,
        "plan_digest": sha256_json(plan_without_digest),
    }


def validate_plan(plan: Mapping[str, object]) -> dict[str, object]:
    if set(plan) != _PLAN_FIELDS or plan.get("schema_version") not in {
        PLAN_SCHEMA,
        PLAN_SCHEMA_V2,
    }:
        raise ExperimentError("experiment plan schema or fields are invalid")
    if not isinstance(plan.get("spec"), Mapping):
        raise ExperimentError("experiment plan spec is absent")
    regenerated = generate_plan(cast(Mapping[str, object], plan["spec"]))
    if dict(plan) != regenerated:
        raise ExperimentError("experiment plan differs from deterministic preregistration")
    return regenerated


def sign_plan(
    plan: Mapping[str, object],
    *,
    private_key: Ed25519PrivateKey,
    issuer_key_id: str,
) -> dict[str, object]:
    validated = validate_plan(plan)
    issuer = identifier(issuer_key_id, "issuer_key_id")
    raw = canonical_bytes(validated)
    is_v2 = validated["schema_version"] == PLAN_SCHEMA_V2
    return {
        "schema_version": SIGNED_PLAN_SCHEMA_V2 if is_v2 else SIGNED_PLAN_SCHEMA,
        "payload": validated,
        "plan_hash": sha256_bytes(raw),
        "issuer_key_id": issuer,
        "signature": base64.b64encode(
            private_key.sign(
                (PLAN_SIGNATURE_DOMAIN_V2 if is_v2 else PLAN_SIGNATURE_DOMAIN) + raw
            )
        ).decode("ascii"),
    }


def verify_plan_envelope(
    envelope: Mapping[str, object],
    trusted_keys: Mapping[str, bytes],
) -> dict[str, object]:
    if (
        set(envelope) != _PLAN_ENVELOPE_FIELDS
        or envelope.get("schema_version") not in {
            SIGNED_PLAN_SCHEMA,
            SIGNED_PLAN_SCHEMA_V2,
        }
    ):
        raise ExperimentError("signed plan envelope fields are invalid")
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping):
        raise ExperimentError("signed plan payload is absent")
    plan = validate_plan(payload)
    raw = canonical_bytes(plan)
    is_v2 = plan["schema_version"] == PLAN_SCHEMA_V2
    expected_envelope_schema = SIGNED_PLAN_SCHEMA_V2 if is_v2 else SIGNED_PLAN_SCHEMA
    if envelope.get("schema_version") != expected_envelope_schema:
        raise ExperimentError("signed plan schema version mismatch")
    if envelope.get("plan_hash") != sha256_bytes(raw):
        raise ExperimentError("signed plan hash mismatch")
    key_id = identifier(envelope.get("issuer_key_id"), "plan issuer_key_id")
    key = trusted_keys.get(key_id)
    if not isinstance(key, bytes) or len(key) != 32:
        raise ExperimentError("signed plan issuer is not trusted")
    signature_value = envelope.get("signature")
    if not isinstance(signature_value, str):
        raise ExperimentError("signed plan signature is invalid")
    try:
        signature = base64.b64decode(signature_value, validate=True)
        Ed25519PublicKey.from_public_bytes(key).verify(
            signature,
            (PLAN_SIGNATURE_DOMAIN_V2 if is_v2 else PLAN_SIGNATURE_DOMAIN) + raw,
        )
    except (InvalidSignature, ValueError, TypeError, binascii.Error) as exc:
        raise ExperimentError("signed plan signature is invalid") from exc
    return plan


__all__ = [
    "generate_plan",
    "sign_plan",
    "validate_plan",
    "verify_plan_envelope",
]
