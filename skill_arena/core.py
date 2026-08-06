from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Literal, Mapping, NotRequired, Sequence, TypeAlias, TypedDict, cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SANDBOX_CASE_RECEIPT_SCHEMA = json.loads(
    (ROOT / "contracts/sandbox-case-receipt.schema.json").read_text(encoding="utf-8")
)
MANAGED_EXECUTION_RECEIPT_SCHEMA = json.loads(
    (ROOT / "contracts/managed-execution-receipt.schema.json").read_text(
        encoding="utf-8"
    )
)
QUALIFICATION_COST_RECEIPT_SCHEMA = json.loads(
    (ROOT / "contracts/qualification-cost-receipt.schema.json").read_text(
        encoding="utf-8"
    )
)
DESIGN_PARTNER_COMMERCIAL_EVIDENCE_SCHEMA = json.loads(
    (ROOT / "contracts/design-partner-commercial-evidence.schema.json").read_text(
        encoding="utf-8"
    )
)
SANDBOX_CASE_SIGNATURE_DOMAIN = b"sandbox-case-receipt@1\n"
MANAGED_EXECUTION_SIGNATURE_DOMAIN = b"managed-execution-receipt@1\n"
QUALIFICATION_COST_SIGNATURE_DOMAIN = b"qualification-cost-receipt@1\n"
DESIGN_PARTNER_COMMERCIAL_SIGNATURE_DOMAIN = (
    b"design-partner-commercial-evidence@1\n"
)
MAXIMUM_EVIDENCE_LIFETIME = timedelta(hours=24)


class TraceRejected(ValueError):
    pass


class CandidateRejected(ValueError):
    pass


class EvidenceRejected(ValueError):
    pass


class HardGateResult(TypedDict):
    promotion_allowed: bool
    failed_gates: list[str]
    target_success_rate: float
    target_success_rate_ppm: int
    target_success_threshold_ppm: int
    llm_judge: str | None
    llm_judge_authority: Literal["advisory_only"]


JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class SignedEnvelope(TypedDict):
    payload: dict[str, object]
    issuer_key_id: str
    signature: str
    gate_receipt_hash: NotRequired[str]
    receipt_hash: NotRequired[str]
    snapshot_hash: NotRequired[str]


class HardGateCase(TypedDict):
    case_id: str
    group: Literal["critical", "anchor", "target"]
    passed: bool
    evidence_digest: str


@dataclass(frozen=True)
class VerifiedSandboxBatch:
    cases: tuple[HardGateCase, ...]
    receipt_set_digest: str


@dataclass(frozen=True)
class VerifiedExecutionFeedback:
    receipt_hash: str
    receipt_id: str
    decision_hash: str
    artifact_digest: str
    host_profile_id: str
    attestation_evidence_digest: str
    feedback_digest: str
    execution_attempt_id: str
    outcome: Literal["succeeded", "failed"]
    expires_at: datetime


@dataclass(frozen=True)
class VerifiedQualificationCost:
    receipt_hash: str
    receipt_id: str
    issuer_key_id: str
    issuer_public_key_hash: str
    qualification_receipt_id: str
    skill_artifact_digest: str
    host_profile_id: str
    host_version: str
    transport_profile: str
    policy_profile: str
    metering_profile_id: str
    measurement_evidence_digest: str
    pricing_policy_digest: str
    total_cost_microunits: int
    expires_at: datetime


@dataclass(frozen=True)
class QualificationCostExpectation:
    qualification_receipt_id: str
    host_profile_id: str
    host_version: str
    transport_profile: str
    policy_profile: str
    host_attestation_evidence_digest: str
    runner_digest: str
    environment_image_digest: str
    metering_profile_id: str


@dataclass(frozen=True)
class QualificationCostEvidence:
    receipt: object
    trusted_keys: Mapping[str, bytes]
    expectation: QualificationCostExpectation
    skill_artifact_digest: str
    benchmark_suite_digest: str
    sandbox_receipt_set_digest: str
    measurement_evidence_digest: str
    pricing_policy_digest: str


@dataclass(frozen=True)
class CommercialEvidenceExpectation:
    pilot_id: str
    design_partner_subject_hash: str
    pricing_policy_digest: str
    terms_evidence_digest: str


@dataclass(frozen=True)
class VerifiedCommercialEvidence:
    receipt_hash: str
    receipt_id: str
    issuer_public_key_hash: str
    pilot_id: str
    design_partner_subject_hash: str
    pricing_policy_digest: str
    terms_evidence_digest: str
    offered_price_microunits: int
    minimum_committed_qualifications: int
    expires_at: datetime


class UnitEconomicsResult(TypedDict):
    sample_count: int
    p95_delivery_cost_microunits: int
    offered_price_microunits: int
    price_above_p95: bool
    productization_allowed: bool


class SkillManifest(TypedDict):
    skill_id: str
    skill_version: NotRequired[str]
    artifact_digest: str
    capabilities: list[str]
    non_capabilities: list[str]
    required_host_capabilities: NotRequired[list[str]]
    permissions: list[str]
    supported_profiles: NotRequired[dict[str, list[str]]]
    positive_exemplars: list[str]
    negative_exemplars: list[str]
    fallback_skill_ids: NotRequired[list[str]]
    qualification_receipt_id: str


class RankingPolicyInput(TypedDict):
    minimum_score: int
    minimum_margin: int


class TaxonomyPolicyInput(TypedDict):
    schema_version: Literal["capability-taxonomy@1"]
    taxonomy_version: str
    core_capabilities: list[str]
    customer_namespaces: list[str]
    aliases: dict[str, str]
    deprecations: dict[str, str | None]


class RevocationPolicyInput(TypedDict):
    schema_version: Literal["revocation-freshness@1"]
    source_sequence: int
    source_observed_at: str
    propagation_target_seconds: int


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _evidence_time(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise EvidenceRejected(f"invalid evidence {label}") from exc
    if parsed.tzinfo is None:
        raise EvidenceRejected(f"evidence {label} must include timezone")
    return parsed


def _verify_evidence_envelope(
    receipt: object,
    trusted_keys: Mapping[str, bytes],
    *,
    schema: object,
    label: str,
    signature_domain: bytes,
) -> tuple[dict[str, object], str, str]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(receipt),
        key=lambda item: list(item.path),
    )
    if errors:
        path = ".".join(str(part) for part in errors[0].path) or "<root>"
        raise EvidenceRejected(
            f"{label} schema error at {path}: {errors[0].message}"
        )
    envelope = cast(dict[str, object], receipt)
    payload = cast(dict[str, object], envelope["payload"])
    raw = canonical_bytes(payload)
    receipt_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    if envelope["receipt_hash"] != receipt_hash:
        raise EvidenceRejected(f"{label} hash mismatch")
    key_id = cast(str, envelope["issuer_key_id"])
    if key_id not in trusted_keys:
        raise EvidenceRejected(f"{label} issuer is not trusted")
    try:
        Ed25519PublicKey.from_public_bytes(trusted_keys[key_id]).verify(
            base64.b64decode(cast(str, envelope["signature"]), validate=True),
            signature_domain + raw,
        )
    except (InvalidSignature, ValueError, binascii.Error) as exc:
        raise EvidenceRejected(f"{label} signature is invalid") from exc
    return payload, receipt_hash, key_id


def verify_sandbox_case_receipts(
    receipts: Sequence[object],
    trusted_keys: Mapping[str, bytes],
    *,
    expected_cases: Sequence[Mapping[str, object]],
    expected_benchmark_suite_digest: str,
    expected_skill_artifact_digest: str,
    expected_target_host_profile_id: str,
    expected_target_host_version: str,
    expected_target_transport_profile: str,
    expected_target_policy_profile: str,
    expected_sandbox_profile_id: str,
    expected_sandbox_image_digest: str,
    expected_sandbox_policy_digest: str,
    expected_artifact_access_policy: str,
    expected_workspace_disposition: str,
    expected_secret_policy: str,
    expected_allowed_tools_digest: str,
    expected_cpu_time_ms: int,
    expected_wall_time_ms: int,
    expected_memory_bytes: int,
    expected_process_count_max: int,
    expected_network_policy: str,
    now: datetime | None = None,
) -> VerifiedSandboxBatch:
    current = now or datetime.now(timezone.utc)
    expected_by_id = {
        str(case.get("case_id")): case
        for case in expected_cases
        if isinstance(case.get("case_id"), str)
    }
    if len(expected_by_id) != len(expected_cases) or len(receipts) != len(expected_cases):
        raise EvidenceRejected("sandbox receipt set does not cover the exact case set")
    verified_cases: list[HardGateCase] = []
    receipt_identities: list[dict[str, str]] = []
    seen_receipt_ids: set[str] = set()
    seen_case_ids: set[str] = set()
    for receipt in receipts:
        payload, receipt_hash, key_id = _verify_evidence_envelope(
            receipt,
            trusted_keys,
            schema=SANDBOX_CASE_RECEIPT_SCHEMA,
            label="sandbox receipt",
            signature_domain=SANDBOX_CASE_SIGNATURE_DOMAIN,
        )
        started_at = _evidence_time(payload["started_at"], "started_at")
        completed_at = _evidence_time(payload["completed_at"], "completed_at")
        issued_at = _evidence_time(payload["issued_at"], "issued_at")
        expires_at = _evidence_time(payload["expires_at"], "expires_at")
        limits = cast(dict[str, object], payload["resource_limits"])
        if (
            started_at > completed_at
            or completed_at > issued_at
            or issued_at > current
            or expires_at <= current
            or expires_at <= issued_at
            or expires_at - issued_at > MAXIMUM_EVIDENCE_LIFETIME
            or (completed_at - started_at).total_seconds() * 1000
            > cast(int, limits["wall_time_ms"])
        ):
            raise EvidenceRejected("sandbox receipt validity or runtime window is invalid")
        case_id = cast(str, payload["case_id"])
        expected_case = expected_by_id.get(case_id)
        expected_bindings = {
            "benchmark_suite_digest": expected_benchmark_suite_digest,
            "skill_artifact_digest": expected_skill_artifact_digest,
            "target_host_profile_id": expected_target_host_profile_id,
            "target_host_version": expected_target_host_version,
            "target_transport_profile": expected_target_transport_profile,
            "target_policy_profile": expected_target_policy_profile,
            "sandbox_profile_id": expected_sandbox_profile_id,
            "sandbox_image_digest": expected_sandbox_image_digest,
            "sandbox_policy_digest": expected_sandbox_policy_digest,
            "artifact_access_policy": expected_artifact_access_policy,
            "workspace_disposition": expected_workspace_disposition,
            "secret_policy": expected_secret_policy,
            "allowed_tools_digest": expected_allowed_tools_digest,
        }
        if (
            expected_case is None
            or any(payload.get(field) != value for field, value in expected_bindings.items())
            or payload["case_input_digest"]
            != "sha256:" + hashlib.sha256(canonical_bytes(expected_case)).hexdigest()
            or limits["cpu_time_ms"] != expected_cpu_time_ms
            or limits["wall_time_ms"] != expected_wall_time_ms
            or limits["memory_bytes"] != expected_memory_bytes
            or limits["process_count_max"] != expected_process_count_max
            or limits["network_policy"] != expected_network_policy
            or payload["case_group"] != expected_case.get("group")
            or payload["passed"] != expected_case.get("passed")
            or payload["output_evidence_digest"] != expected_case.get("evidence_digest")
        ):
            raise EvidenceRejected("sandbox receipt binding mismatch")
        receipt_id = cast(str, payload["receipt_id"])
        if receipt_id in seen_receipt_ids or case_id in seen_case_ids:
            raise EvidenceRejected("duplicate sandbox receipt or case id")
        seen_receipt_ids.add(receipt_id)
        seen_case_ids.add(case_id)
        verified_cases.append(
            {
                "case_id": case_id,
                "group": cast(Literal["critical", "anchor", "target"], payload["case_group"]),
                "passed": cast(bool, payload["passed"]),
                "evidence_digest": cast(str, payload["output_evidence_digest"]),
            }
        )
        receipt_identities.append(
            {"case_id": case_id, "issuer_key_id": key_id, "receipt_hash": receipt_hash}
        )
    if seen_case_ids != set(expected_by_id):
        raise EvidenceRejected("sandbox receipt set does not cover the exact case set")
    receipt_set_digest = "sha256:" + hashlib.sha256(
        canonical_bytes(sorted(receipt_identities, key=lambda item: item["case_id"]))
    ).hexdigest()
    return VerifiedSandboxBatch(tuple(verified_cases), receipt_set_digest)


def verify_execution_feedback_receipt(
    receipt: object,
    trusted_keys: Mapping[str, bytes],
    *,
    expected_authorization_receipt_hash: str,
    expected_decision_hash: str,
    expected_workload_identity_receipt_hash: str,
    expected_execution_attempt_id: str,
    expected_skill_id: str,
    expected_artifact_digest: str,
    expected_qualification_receipt_id: str,
    expected_snapshot_hash: str,
    expected_host_profile_id: str,
    expected_carrier_id: str,
    expected_attestation_evidence_digest: str,
    expected_nonce_consumption_evidence_digest: str,
    expected_feedback_digest: str,
    expected_outcome: Literal["succeeded", "failed"],
    expected_audience: str,
    now: datetime | None = None,
) -> VerifiedExecutionFeedback:
    payload, receipt_hash, _key_id = _verify_evidence_envelope(
        receipt,
        trusted_keys,
        schema=MANAGED_EXECUTION_RECEIPT_SCHEMA,
        label="execution receipt",
        signature_domain=MANAGED_EXECUTION_SIGNATURE_DOMAIN,
    )
    current = now or datetime.now(timezone.utc)
    started_at = _evidence_time(payload["started_at"], "started_at")
    completed_at = _evidence_time(payload["completed_at"], "completed_at")
    issued_at = _evidence_time(payload["issued_at"], "issued_at")
    expires_at = _evidence_time(payload["expires_at"], "expires_at")
    if (
        started_at > completed_at
        or completed_at > issued_at
        or issued_at > current
        or expires_at <= current
        or expires_at <= issued_at
        or expires_at - issued_at > MAXIMUM_EVIDENCE_LIFETIME
    ):
        raise EvidenceRejected("execution receipt validity window is invalid")
    expected_bindings = {
        "authorization_receipt_hash": expected_authorization_receipt_hash,
        "decision_hash": expected_decision_hash,
        "workload_identity_receipt_hash": expected_workload_identity_receipt_hash,
        "execution_attempt_id": expected_execution_attempt_id,
        "skill_id": expected_skill_id,
        "artifact_digest": expected_artifact_digest,
        "qualification_receipt_id": expected_qualification_receipt_id,
        "snapshot_hash": expected_snapshot_hash,
        "host_profile_id": expected_host_profile_id,
        "carrier_id": expected_carrier_id,
        "attestation_evidence_digest": expected_attestation_evidence_digest,
        "nonce_consumption_evidence_digest": (
            expected_nonce_consumption_evidence_digest
        ),
        "feedback_digest": expected_feedback_digest,
        "outcome": expected_outcome,
        "audience": expected_audience,
    }
    if any(payload.get(field) != value for field, value in expected_bindings.items()):
        raise EvidenceRejected("execution receipt binding mismatch")
    return VerifiedExecutionFeedback(
        receipt_hash=receipt_hash,
        receipt_id=cast(str, payload["receipt_id"]),
        decision_hash=cast(str, payload["decision_hash"]),
        artifact_digest=cast(str, payload["artifact_digest"]),
        host_profile_id=cast(str, payload["host_profile_id"]),
        attestation_evidence_digest=cast(
            str, payload["attestation_evidence_digest"]
        ),
        feedback_digest=cast(str, payload["feedback_digest"]),
        execution_attempt_id=cast(str, payload["execution_attempt_id"]),
        outcome=cast(Literal["succeeded", "failed"], payload["outcome"]),
        expires_at=expires_at,
    )


def verify_qualification_cost_receipt(
    receipt: object,
    trusted_keys: Mapping[str, bytes],
    *,
    expected_qualification_receipt_id: str,
    expected_skill_artifact_digest: str,
    expected_benchmark_suite_digest: str,
    expected_sandbox_receipt_set_digest: str,
    expected_host_profile_id: str,
    expected_host_version: str,
    expected_transport_profile: str,
    expected_policy_profile: str,
    expected_host_attestation_evidence_digest: str,
    expected_runner_digest: str,
    expected_environment_image_digest: str,
    expected_measurement_evidence_digest: str,
    expected_metering_profile_id: str,
    expected_pricing_policy_digest: str,
    now: datetime | None = None,
) -> VerifiedQualificationCost:
    payload, receipt_hash, key_id = _verify_evidence_envelope(
        receipt,
        trusted_keys,
        schema=QUALIFICATION_COST_RECEIPT_SCHEMA,
        label="qualification cost receipt",
        signature_domain=QUALIFICATION_COST_SIGNATURE_DOMAIN,
    )
    current = now or datetime.now(timezone.utc)
    measurement_started_at = _evidence_time(
        payload["measurement_started_at"], "measurement_started_at"
    )
    measurement_completed_at = _evidence_time(
        payload["measurement_completed_at"], "measurement_completed_at"
    )
    issued_at = _evidence_time(payload["issued_at"], "issued_at")
    expires_at = _evidence_time(payload["expires_at"], "expires_at")
    if (
        measurement_started_at > measurement_completed_at
        or measurement_completed_at > issued_at
        or issued_at > current
        or expires_at <= current
        or expires_at <= issued_at
        or expires_at - issued_at > MAXIMUM_EVIDENCE_LIFETIME
    ):
        raise EvidenceRejected("qualification cost validity window is invalid")
    expected_bindings = {
        "qualification_receipt_id": expected_qualification_receipt_id,
        "skill_artifact_digest": expected_skill_artifact_digest,
        "benchmark_suite_digest": expected_benchmark_suite_digest,
        "sandbox_receipt_set_digest": expected_sandbox_receipt_set_digest,
        "host_profile_id": expected_host_profile_id,
        "host_version": expected_host_version,
        "transport_profile": expected_transport_profile,
        "policy_profile": expected_policy_profile,
        "host_attestation_evidence_digest": (
            expected_host_attestation_evidence_digest
        ),
        "runner_digest": expected_runner_digest,
        "environment_image_digest": expected_environment_image_digest,
        "measurement_evidence_digest": expected_measurement_evidence_digest,
        "metering_profile_id": expected_metering_profile_id,
        "pricing_policy_digest": expected_pricing_policy_digest,
    }
    if any(payload.get(field) != value for field, value in expected_bindings.items()):
        raise EvidenceRejected("qualification cost receipt binding mismatch")
    costs = cast(dict[str, int], payload["costs"])
    total_cost = cast(int, payload["total_cost_microunits"])
    if sum(costs.values()) != total_cost:
        raise EvidenceRejected("qualification cost total mismatch")
    return VerifiedQualificationCost(
        receipt_hash=receipt_hash,
        receipt_id=cast(str, payload["receipt_id"]),
        issuer_key_id=key_id,
        issuer_public_key_hash=(
            "sha256:" + hashlib.sha256(trusted_keys[key_id]).hexdigest()
        ),
        qualification_receipt_id=cast(str, payload["qualification_receipt_id"]),
        skill_artifact_digest=cast(str, payload["skill_artifact_digest"]),
        host_profile_id=cast(str, payload["host_profile_id"]),
        host_version=cast(str, payload["host_version"]),
        transport_profile=cast(str, payload["transport_profile"]),
        policy_profile=cast(str, payload["policy_profile"]),
        metering_profile_id=cast(str, payload["metering_profile_id"]),
        measurement_evidence_digest=cast(
            str, payload["measurement_evidence_digest"]
        ),
        pricing_policy_digest=cast(str, payload["pricing_policy_digest"]),
        total_cost_microunits=total_cost,
        expires_at=expires_at,
    )


def verify_design_partner_commercial_evidence(
    receipt: object,
    trusted_keys: Mapping[str, bytes],
    *,
    expected_pilot_id: str,
    expected_design_partner_subject_hash: str,
    expected_pricing_policy_digest: str,
    expected_terms_evidence_digest: str,
    now: datetime | None = None,
) -> VerifiedCommercialEvidence:
    payload, receipt_hash, key_id = _verify_evidence_envelope(
        receipt,
        trusted_keys,
        schema=DESIGN_PARTNER_COMMERCIAL_EVIDENCE_SCHEMA,
        label="design partner commercial evidence",
        signature_domain=DESIGN_PARTNER_COMMERCIAL_SIGNATURE_DOMAIN,
    )
    current = now or datetime.now(timezone.utc)
    issued_at = _evidence_time(payload["issued_at"], "issued_at")
    expires_at = _evidence_time(payload["expires_at"], "expires_at")
    if (
        issued_at > current
        or expires_at <= current
        or expires_at <= issued_at
        or expires_at - issued_at > MAXIMUM_EVIDENCE_LIFETIME
    ):
        raise EvidenceRejected("design partner commercial validity window is invalid")
    expected_bindings = {
        "pilot_id": expected_pilot_id,
        "design_partner_subject_hash": expected_design_partner_subject_hash,
        "pricing_policy_digest": expected_pricing_policy_digest,
        "terms_evidence_digest": expected_terms_evidence_digest,
    }
    if any(payload.get(field) != value for field, value in expected_bindings.items()):
        raise EvidenceRejected("design partner commercial evidence binding mismatch")
    return VerifiedCommercialEvidence(
        receipt_hash=receipt_hash,
        receipt_id=cast(str, payload["receipt_id"]),
        issuer_public_key_hash=(
            "sha256:" + hashlib.sha256(trusted_keys[key_id]).hexdigest()
        ),
        pilot_id=cast(str, payload["pilot_id"]),
        design_partner_subject_hash=cast(
            str, payload["design_partner_subject_hash"]
        ),
        pricing_policy_digest=cast(str, payload["pricing_policy_digest"]),
        terms_evidence_digest=cast(str, payload["terms_evidence_digest"]),
        offered_price_microunits=cast(
            int, payload["offered_price_per_qualification_microunits"]
        ),
        minimum_committed_qualifications=cast(
            int, payload["minimum_committed_qualifications"]
        ),
        expires_at=expires_at,
    )


def _evaluate_verified_unit_economics(
    cost_receipts: Sequence[VerifiedQualificationCost],
    commercial_evidence: VerifiedCommercialEvidence,
    *,
    minimum_cost_receipts: int,
    now: datetime | None = None,
) -> UnitEconomicsResult:
    if minimum_cost_receipts < 1 or len(cost_receipts) < minimum_cost_receipts:
        raise EvidenceRejected("unit economics cost cohort is too small")
    current = now or datetime.now(timezone.utc)
    if commercial_evidence.expires_at <= current:
        raise EvidenceRejected("unit economics commercial evidence is expired")
    if any(
        item.expires_at <= current
        or item.pricing_policy_digest != commercial_evidence.pricing_policy_digest
        for item in cost_receipts
    ):
        raise EvidenceRejected("unit economics cohort binding or freshness mismatch")
    if len({item.receipt_hash for item in cost_receipts}) != len(cost_receipts):
        raise EvidenceRejected("unit economics cost cohort contains duplicate receipts")
    cohort_dimensions = {
        (
            item.host_profile_id,
            item.host_version,
            item.transport_profile,
            item.policy_profile,
            item.metering_profile_id,
            item.pricing_policy_digest,
        )
        for item in cost_receipts
    }
    if len(cohort_dimensions) != 1:
        raise EvidenceRejected("unit economics cost cohort mixes physical profiles")
    if any(
        item.issuer_public_key_hash == commercial_evidence.issuer_public_key_hash
        for item in cost_receipts
    ):
        raise EvidenceRejected(
            "cost and design partner commercial issuer keys must be distinct"
        )
    ordered_costs = sorted(item.total_cost_microunits for item in cost_receipts)
    p95_index = ((95 * len(ordered_costs) + 99) // 100) - 1
    p95_cost = ordered_costs[p95_index]
    offered_price = commercial_evidence.offered_price_microunits
    price_above_p95 = offered_price > p95_cost
    return {
        "sample_count": len(ordered_costs),
        "p95_delivery_cost_microunits": p95_cost,
        "offered_price_microunits": offered_price,
        "price_above_p95": price_above_p95,
        "productization_allowed": price_above_p95,
    }


def evaluate_unit_economics(
    cost_evidence: Sequence[QualificationCostEvidence],
    commercial_receipt: object,
    commercial_trusted_keys: Mapping[str, bytes],
    commercial_expectation: CommercialEvidenceExpectation,
    *,
    minimum_cost_receipts: int,
    now: datetime | None = None,
) -> UnitEconomicsResult:
    current = now or datetime.now(timezone.utc)
    verified_costs = [
        verify_qualification_cost_receipt(
            item.receipt,
            item.trusted_keys,
            expected_qualification_receipt_id=(
                item.expectation.qualification_receipt_id
            ),
            expected_skill_artifact_digest=item.skill_artifact_digest,
            expected_benchmark_suite_digest=item.benchmark_suite_digest,
            expected_sandbox_receipt_set_digest=item.sandbox_receipt_set_digest,
            expected_host_profile_id=item.expectation.host_profile_id,
            expected_host_version=item.expectation.host_version,
            expected_transport_profile=item.expectation.transport_profile,
            expected_policy_profile=item.expectation.policy_profile,
            expected_host_attestation_evidence_digest=(
                item.expectation.host_attestation_evidence_digest
            ),
            expected_runner_digest=item.expectation.runner_digest,
            expected_environment_image_digest=(
                item.expectation.environment_image_digest
            ),
            expected_measurement_evidence_digest=item.measurement_evidence_digest,
            expected_metering_profile_id=item.expectation.metering_profile_id,
            expected_pricing_policy_digest=item.pricing_policy_digest,
            now=current,
        )
        for item in cost_evidence
    ]
    commercial = verify_design_partner_commercial_evidence(
        commercial_receipt,
        commercial_trusted_keys,
        expected_pilot_id=commercial_expectation.pilot_id,
        expected_design_partner_subject_hash=(
            commercial_expectation.design_partner_subject_hash
        ),
        expected_pricing_policy_digest=commercial_expectation.pricing_policy_digest,
        expected_terms_evidence_digest=commercial_expectation.terms_evidence_digest,
        now=current,
    )
    return _evaluate_verified_unit_economics(
        verified_costs,
        commercial,
        minimum_cost_receipts=minimum_cost_receipts,
        now=current,
    )


def _signed(
    payload: dict[str, object],
    key_id: str,
    private_key: Ed25519PrivateKey,
    hash_field: Literal["gate_receipt_hash", "receipt_hash", "snapshot_hash"],
) -> SignedEnvelope:
    raw = canonical_bytes(payload)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    return cast(
        SignedEnvelope,
        {"payload": payload, hash_field: digest, "issuer_key_id": key_id, "signature": base64.b64encode(private_key.sign(raw)).decode()},
    )


def issue_promotion_gate_receipt(
    *,
    skill_artifact_digest: str,
    benchmark_suite_digest: str,
    hard_gates: HardGateResult,
    budget_gates: PromotionBudgetResult,
    sandbox_receipt_set_digest: str,
    qualification_cost_receipt: object,
    qualification_cost_trusted_keys: Mapping[str, bytes],
    qualification_cost_expectation: QualificationCostExpectation,
    measurement_evidence_digest: str,
    budget_policy_digest: str,
    issuer_key_id: str,
    private_key: Ed25519PrivateKey,
) -> SignedEnvelope:
    if not hard_gates["promotion_allowed"] or not budget_gates["promotion_allowed"]:
        raise ValueError("failed promotion gates cannot be signed")
    if (
        hard_gates["target_success_threshold_ppm"]
        != budget_gates["budgets"].target_success_rate_ppm_min
    ):
        raise ValueError("hard-gate threshold does not match signed budget policy")
    verified_cost = verify_qualification_cost_receipt(
        qualification_cost_receipt,
        qualification_cost_trusted_keys,
        expected_qualification_receipt_id=(
            qualification_cost_expectation.qualification_receipt_id
        ),
        expected_skill_artifact_digest=skill_artifact_digest,
        expected_benchmark_suite_digest=benchmark_suite_digest,
        expected_sandbox_receipt_set_digest=sandbox_receipt_set_digest,
        expected_host_profile_id=qualification_cost_expectation.host_profile_id,
        expected_host_version=qualification_cost_expectation.host_version,
        expected_transport_profile=qualification_cost_expectation.transport_profile,
        expected_policy_profile=qualification_cost_expectation.policy_profile,
        expected_host_attestation_evidence_digest=(
            qualification_cost_expectation.host_attestation_evidence_digest
        ),
        expected_runner_digest=qualification_cost_expectation.runner_digest,
        expected_environment_image_digest=(
            qualification_cost_expectation.environment_image_digest
        ),
        expected_measurement_evidence_digest=measurement_evidence_digest,
        expected_metering_profile_id=qualification_cost_expectation.metering_profile_id,
        expected_pricing_policy_digest=budget_policy_digest,
    )
    if verified_cost.total_cost_microunits != budget_gates["measurements"].cost_microunits:
        raise ValueError("promotion measured cost does not match qualification cost receipt")
    cost_issuer_key = qualification_cost_trusted_keys[verified_cost.issuer_key_id]
    if cost_issuer_key == private_key.public_key().public_bytes_raw():
        raise ValueError("qualification cost and promotion issuer keys must be distinct")
    for value in (
        skill_artifact_digest,
        benchmark_suite_digest,
        sandbox_receipt_set_digest,
        measurement_evidence_digest,
        budget_policy_digest,
    ):
        if not _is_sha256(value):
            raise ValueError("promotion gate bindings must be sha256 digests")
    payload = {
        "schema_version": "promotion-gate-receipt@1",
        "qualification_receipt_id": qualification_cost_expectation.qualification_receipt_id,
        "skill_artifact_digest": skill_artifact_digest,
        "benchmark_suite_digest": benchmark_suite_digest,
        "sandbox_receipt_set_digest": sandbox_receipt_set_digest,
        "qualification_cost_receipt_hash": verified_cost.receipt_hash,
        "qualification_cost_issuer_public_key_hash": (
            "sha256:" + hashlib.sha256(cost_issuer_key).hexdigest()
        ),
        "measurement_evidence_digest": measurement_evidence_digest,
        "budget_policy_digest": budget_policy_digest,
        "target_success_rate_ppm": hard_gates["target_success_rate_ppm"],
        "target_success_rate_ppm_min": hard_gates[
            "target_success_threshold_ppm"
        ],
        "p95_latency_us": budget_gates["measurements"].p95_latency_us,
        "cost_microunits": budget_gates["measurements"].cost_microunits,
        "promotion_allowed": True,
    }
    return _signed(payload, issuer_key_id, private_key, "gate_receipt_hash")


def _verify_promotion_gate_receipt(
    receipt: Mapping[str, object],
    trusted_keys: Mapping[str, bytes],
    *,
    qualification_receipt_id: str,
    skill_artifact_digest: str,
    benchmark_suite_digest: str,
) -> str:
    if set(receipt) != {"payload", "gate_receipt_hash", "issuer_key_id", "signature"}:
        raise ValueError("promotion gate envelope has unknown or missing fields")
    payload = receipt.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("promotion gate payload is missing")
    if set(payload) != {
        "schema_version",
        "qualification_receipt_id",
        "skill_artifact_digest",
        "benchmark_suite_digest",
        "sandbox_receipt_set_digest",
        "qualification_cost_receipt_hash",
        "qualification_cost_issuer_public_key_hash",
        "measurement_evidence_digest",
        "budget_policy_digest",
        "target_success_rate_ppm",
        "target_success_rate_ppm_min",
        "p95_latency_us",
        "cost_microunits",
        "promotion_allowed",
    }:
        raise ValueError("promotion gate payload has unknown or missing fields")
    raw = canonical_bytes(payload)
    receipt_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    if receipt.get("gate_receipt_hash") != receipt_hash:
        raise ValueError("promotion gate receipt hash mismatch")
    key_id = receipt.get("issuer_key_id")
    if not isinstance(key_id, str) or key_id not in trusted_keys:
        raise ValueError("promotion gate issuer is not trusted")
    try:
        Ed25519PublicKey.from_public_bytes(trusted_keys[key_id]).verify(
            base64.b64decode(str(receipt.get("signature", "")), validate=True), raw
        )
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("promotion gate signature is invalid") from exc
    expected = {
        "schema_version": "promotion-gate-receipt@1",
        "qualification_receipt_id": qualification_receipt_id,
        "skill_artifact_digest": skill_artifact_digest,
        "benchmark_suite_digest": benchmark_suite_digest,
        "promotion_allowed": True,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("promotion gate receipt is not bound to this qualification")
    if (
        not _is_sha256(payload.get("sandbox_receipt_set_digest"))
        or not _is_sha256(payload.get("qualification_cost_receipt_hash"))
        or not _is_sha256(
            payload.get("qualification_cost_issuer_public_key_hash")
        )
        or not _is_sha256(payload.get("measurement_evidence_digest"))
        or not _is_sha256(payload.get("budget_policy_digest"))
    ):
        raise ValueError("promotion gate evidence bindings are invalid")
    target_rate = payload.get("target_success_rate_ppm")
    target_minimum = payload.get("target_success_rate_ppm_min")
    if (
        type(target_rate) is not int
        or type(target_minimum) is not int
        or not 0 <= target_rate <= 1_000_000
        or not 1 <= target_minimum <= 1_000_000
        or target_rate < target_minimum
    ):
        raise ValueError("promotion gate hard-gate policy is invalid")
    return receipt_hash


def issue_qualification_receipt(*, receipt_id: str, skill_artifact_digest: str, host_profile_id: str, host_version_range: str, transport_profile: str, policy_profile: str, benchmark_suite_digest: str, evaluator_version: str, measured_metrics: Mapping[str, int], issued_at: datetime, expires_at: datetime, issuer_key_id: str, private_key: Ed25519PrivateKey, human_admit: bool, promotion_gate_receipt: Mapping[str, object], promotion_gate_trusted_keys: Mapping[str, bytes]) -> SignedEnvelope:
    if not human_admit:
        raise ValueError("human admit is required")
    promotion_gate_receipt_hash = _verify_promotion_gate_receipt(
        promotion_gate_receipt,
        promotion_gate_trusted_keys,
        qualification_receipt_id=receipt_id,
        skill_artifact_digest=skill_artifact_digest,
        benchmark_suite_digest=benchmark_suite_digest,
    )
    gate_payload = cast(dict[str, object], promotion_gate_receipt["payload"])
    qualification_public_key_hash = "sha256:" + hashlib.sha256(
        private_key.public_key().public_bytes_raw()
    ).hexdigest()
    if (
        gate_payload.get("qualification_cost_issuer_public_key_hash")
        == qualification_public_key_hash
    ):
        raise ValueError("qualification cost and qualification issuer keys must be distinct")
    if measured_metrics.get("p95_latency_us") != gate_payload.get("p95_latency_us") or measured_metrics.get(
        "cost_microunits"
    ) != gate_payload.get("cost_microunits"):
        raise ValueError("qualification metrics do not match the signed promotion gate")
    payload = {"schema_version": "qualification-receipt@1", "receipt_id": receipt_id, "skill_artifact_digest": skill_artifact_digest, "host_profile_id": host_profile_id, "host_version_range": host_version_range, "transport_profile": transport_profile, "policy_profile": policy_profile, "benchmark_suite_digest": benchmark_suite_digest, "evaluator_version": evaluator_version, "measured_metrics": dict(measured_metrics), "promotion_gate_receipt_hash": promotion_gate_receipt_hash, "issued_at": issued_at.isoformat(), "expires_at": expires_at.isoformat(), "status": "qualified"}
    return _signed(payload, issuer_key_id, private_key, "receipt_hash")


def publish_snapshot(
    *,
    skills: Sequence[SkillManifest],
    receipts: Sequence[SignedEnvelope],
    revocations: Sequence[str],
    sequence: int,
    issued_at: datetime,
    not_after: datetime,
    taxonomy_version: str,
    taxonomy_policy: TaxonomyPolicyInput | None = None,
    revocation_policy: RevocationPolicyInput | None = None,
    minimum_resolver_build: str | None = None,
    required_features: Sequence[str] | None = None,
    ranking_policy: RankingPolicyInput,
    issuer_key_id: str,
    private_key: Ed25519PrivateKey,
    snapshot_schema_version: Literal["registry-snapshot@1", "registry-snapshot@2"] = (
        "registry-snapshot@2"
    ),
) -> SignedEnvelope:
    supported_schema_versions = {"registry-snapshot@1", "registry-snapshot@2"}
    if snapshot_schema_version not in supported_schema_versions:
        raise ValueError("unsupported snapshot schema")
    v2_manifest_fields = {
        "skill_version",
        "required_host_capabilities",
        "supported_profiles",
        "fallback_skill_ids",
    }
    if snapshot_schema_version == "registry-snapshot@2" and any(
        not v2_manifest_fields.issubset(skill) for skill in skills
    ):
        raise ValueError("registry-snapshot@2 requires the extended skill manifest")
    if snapshot_schema_version == "registry-snapshot@2" and taxonomy_policy is None:
        raise ValueError("registry-snapshot@2 requires a signed taxonomy policy")
    if snapshot_schema_version == "registry-snapshot@2" and revocation_policy is None:
        raise ValueError("registry-snapshot@2 requires a revocation freshness policy")
    if snapshot_schema_version == "registry-snapshot@2" and (
        minimum_resolver_build is None or required_features is None
    ):
        raise ValueError("registry-snapshot@2 requires resolver compatibility fields")
    if snapshot_schema_version == "registry-snapshot@1" and (
        taxonomy_policy is not None
        or revocation_policy is not None
        or minimum_resolver_build is not None
        or required_features is not None
    ):
        raise ValueError("registry-snapshot@1 cannot declare v2 policy fields")
    signed_ranking_policy = {
        "policy_id": "lexical-fixed-point@1",
        "tokenizer_version": "unicode-token@1",
        "normalization_version": "lowercase-token@1",
        "ranking_version": "bm25-char3-evidence@1",
        **ranking_policy,
    }
    payload = {"schema_version": snapshot_schema_version, "sequence": sequence, "issued_at": issued_at.isoformat(), "not_after": not_after.isoformat(), "taxonomy_version": taxonomy_version, "ranking_policy": signed_ranking_policy, "skills": skills, "qualification_receipts": receipts, "revocations": sorted(revocations)}
    if taxonomy_policy is not None:
        payload["taxonomy_policy"] = taxonomy_policy
    if revocation_policy is not None:
        payload["revocation_policy"] = revocation_policy
    if minimum_resolver_build is not None:
        payload["minimum_resolver_build"] = minimum_resolver_build
    if required_features is not None:
        payload["required_features"] = sorted(required_features)
    return _signed(payload, issuer_key_id, private_key, "snapshot_hash")


def evaluate_hard_gates(
    cases: Iterable[Mapping[str, object]],
    *,
    target_success_threshold_ppm: int,
    llm_judge: str | None = None,
) -> HardGateResult:
    if (
        type(target_success_threshold_ppm) is not int
        or not 1 <= target_success_threshold_ppm <= 1_000_000
    ):
        raise ValueError("target success threshold must be 1..1,000,000 ppm")
    rows = list(cases)
    failed: list[str] = []
    expected_fields = {"case_id", "group", "passed", "evidence_digest"}
    if any(set(row) != expected_fields for row in rows):
        failed.append("unknown_or_missing_case_fields")
    allowed_groups = {"critical", "anchor", "target"}
    case_ids = [row.get("case_id") for row in rows]
    groups = {row.get("group") for row in rows}
    if any(not isinstance(case_id, str) or not case_id for case_id in case_ids):
        failed.append("missing_case_id")
    if len(case_ids) != len(set(case_ids)):
        failed.append("duplicate_case_id")
    if not groups <= allowed_groups:
        failed.append("unknown_group")
    for required_group in allowed_groups:
        if required_group not in groups:
            failed.append(f"missing_{required_group}_group")
    if any(type(row.get("passed")) is not bool for row in rows):
        failed.append("untyped_verdict")
    if any(not _is_sha256(row.get("evidence_digest")) for row in rows):
        failed.append("missing_evidence_digest")
    if any(not row.get("passed") for row in rows if row.get("group") == "critical"):
        failed.append("critical_failure")
    if any(not row.get("passed") for row in rows if row.get("group") == "anchor"):
        failed.append("anchor_failure")
    targets = [bool(row.get("passed")) for row in rows if row.get("group") == "target"]
    target_rate = sum(targets) / len(targets) if targets else 0.0
    target_rate_ppm = (
        sum(targets) * 1_000_000 // len(targets) if targets else 0
    )
    if target_rate_ppm < target_success_threshold_ppm:
        failed.append("target_threshold")
    return {
        "promotion_allowed": not failed,
        "failed_gates": failed,
        "target_success_rate": target_rate,
        "target_success_rate_ppm": target_rate_ppm,
        "target_success_threshold_ppm": target_success_threshold_ppm,
        "llm_judge": llm_judge,
        "llm_judge_authority": "advisory_only",
    }


@dataclass(frozen=True)
class PromotionMeasurements:
    p95_latency_us: int
    p99_latency_us: int
    cost_microunits: int
    ambiguous_false_positive_ppm: int
    unsupported_false_positive_ppm: int

    def __post_init__(self) -> None:
        values = (
            self.p95_latency_us,
            self.p99_latency_us,
            self.cost_microunits,
            self.ambiguous_false_positive_ppm,
            self.unsupported_false_positive_ppm,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("promotion measurements must be non-negative integers")
        if self.p99_latency_us < self.p95_latency_us:
            raise ValueError("p99 latency cannot be lower than p95 latency")
        if self.ambiguous_false_positive_ppm > 1_000_000 or self.unsupported_false_positive_ppm > 1_000_000:
            raise ValueError("false-positive rates cannot exceed 1,000,000 ppm")


@dataclass(frozen=True)
class PromotionBudgets:
    p95_latency_us_max: int
    p99_latency_us_max: int
    cost_microunits_max: int
    ambiguous_false_positive_ppm_max: int
    unsupported_false_positive_ppm_max: int
    target_success_rate_ppm_min: int

    def __post_init__(self) -> None:
        values = (
            self.p95_latency_us_max,
            self.p99_latency_us_max,
            self.cost_microunits_max,
            self.ambiguous_false_positive_ppm_max,
            self.unsupported_false_positive_ppm_max,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ValueError("promotion budgets must be non-negative integers")
        if not 1 <= self.target_success_rate_ppm_min <= 1_000_000:
            raise ValueError("target success budget must be 1..1,000,000 ppm")
        if self.p99_latency_us_max < self.p95_latency_us_max:
            raise ValueError("p99 latency budget cannot be lower than p95 latency budget")
        if self.ambiguous_false_positive_ppm_max > 1_000_000 or self.unsupported_false_positive_ppm_max > 1_000_000:
            raise ValueError("false-positive budgets cannot exceed 1,000,000 ppm")


class PromotionBudgetResult(TypedDict):
    promotion_allowed: bool
    failed_gates: list[str]
    measurements: PromotionMeasurements
    budgets: PromotionBudgets
    llm_judge: str | None
    llm_judge_authority: Literal["advisory_only"]


def evaluate_promotion_budgets(
    measurements: PromotionMeasurements,
    budgets: PromotionBudgets,
    *,
    llm_judge: str | None = None,
) -> PromotionBudgetResult:
    """Admit measured evidence only when every preregistered physical budget passes."""
    failed: list[str] = []
    checks = (
        ("p95_latency_budget", measurements.p95_latency_us > budgets.p95_latency_us_max),
        ("p99_latency_budget", measurements.p99_latency_us > budgets.p99_latency_us_max),
        ("cost_budget", measurements.cost_microunits > budgets.cost_microunits_max),
        (
            "ambiguous_false_positive_budget",
            measurements.ambiguous_false_positive_ppm > budgets.ambiguous_false_positive_ppm_max,
        ),
        (
            "unsupported_false_positive_budget",
            measurements.unsupported_false_positive_ppm > budgets.unsupported_false_positive_ppm_max,
        ),
    )
    failed.extend(name for name, did_fail in checks if did_fail)
    return {
        "promotion_allowed": not failed,
        "failed_gates": failed,
        "measurements": measurements,
        "budgets": budgets,
        "llm_judge": llm_judge,
        "llm_judge_authority": "advisory_only",
    }


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


@dataclass(frozen=True)
class MutationCandidate:
    case_id: str
    seed_digest: str
    candidate_digest: str
    candidate_payload: Mapping[str, JSONValue]
    generator_kind: Literal["human", "deterministic", "llm"]
    generator_build: str
    mutation_labels: tuple[str, ...]
    requested_pool: Literal["public", "blind"]


@dataclass(frozen=True)
class OracleEvidence:
    evidence_id: str
    kind: Literal[
        "schema",
        "sandbox",
        "test",
        "structured_diff",
        "formal_solver",
        "llm_advisory",
        "human",
    ]
    verdict: Literal["pass", "fail", "inconclusive"]
    evidence_digest: str
    independent: bool


@dataclass
class CaseIndex:
    case_ids: set[str] = field(default_factory=set)
    candidate_digests: set[str] = field(default_factory=set)
    public_seed_digests: set[str] = field(default_factory=set)
    blind_seed_digests: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class AdmissionReceipt:
    case_id: str
    candidate_digest: str
    state: Literal["candidate", "admitted"]
    pool: Literal["public", "blind"]
    evidence_ids: tuple[str, ...]
    export_allowed: bool


_MUTATION_LABELS = {"boundary", "semantic_noise", "constraint_conflict", "chain_escalation"}
_DETERMINISTIC_ORACLES = {"schema", "sandbox", "test", "structured_diff", "formal_solver"}


def admit_candidate(
    candidate: MutationCandidate,
    evidence: Sequence[OracleEvidence],
    index: CaseIndex,
    *,
    human_admit: bool = False,
) -> AdmissionReceipt:
    if not candidate.case_id or candidate.case_id in index.case_ids:
        raise CandidateRejected("duplicate or empty case_id")
    if not _is_sha256(candidate.seed_digest) or not _is_sha256(candidate.candidate_digest):
        raise CandidateRejected("candidate and seed digests must be sha256")
    measured_digest = "sha256:" + hashlib.sha256(canonical_bytes(candidate.candidate_payload)).hexdigest()
    if measured_digest != candidate.candidate_digest:
        raise CandidateRejected("candidate payload digest mismatch")
    if candidate.candidate_digest in index.candidate_digests:
        raise CandidateRejected("exact duplicate candidate payload")
    if not candidate.mutation_labels or not set(candidate.mutation_labels) <= _MUTATION_LABELS:
        raise CandidateRejected("unknown or empty mutation labels")
    if candidate.requested_pool == "blind" and candidate.seed_digest in index.public_seed_digests:
        raise CandidateRejected("public provenance cannot enter the blind pool")
    if len({item.evidence_id for item in evidence}) != len(evidence):
        raise CandidateRejected("duplicate oracle evidence id")
    if any(not _is_sha256(item.evidence_digest) for item in evidence):
        raise CandidateRejected("oracle evidence digest is required")
    deterministic = [item for item in evidence if item.kind in _DETERMINISTIC_ORACLES]
    if any(item.verdict == "fail" for item in deterministic):
        raise CandidateRejected("deterministic oracle failure cannot be overridden")
    deterministic_pass = any(item.verdict == "pass" and item.independent for item in deterministic)
    human_pass = any(item.kind == "human" and item.verdict == "pass" for item in evidence)
    state: Literal["candidate", "admitted"] = (
        "admitted" if deterministic_pass or (human_admit and human_pass) else "candidate"
    )
    index.case_ids.add(candidate.case_id)
    index.candidate_digests.add(candidate.candidate_digest)
    if candidate.requested_pool == "public":
        index.public_seed_digests.add(candidate.seed_digest)
    else:
        index.blind_seed_digests.add(candidate.seed_digest)
    return AdmissionReceipt(
        case_id=candidate.case_id,
        candidate_digest=candidate.candidate_digest,
        state=state,
        pool=candidate.requested_pool,
        evidence_ids=tuple(item.evidence_id for item in evidence),
        export_allowed=candidate.requested_pool == "public" and state == "admitted",
    )


_TRANSITIONS = {"candidate": {"qualified", "quarantined"}, "qualified": {"deprecated", "suspended", "revoked"}, "deprecated": {"revoked"}, "suspended": {"qualified", "revoked"}, "quarantined": {"candidate", "revoked"}, "revoked": set()}


def apply_lifecycle_transition(current: str, target: str, *, evidence_present: bool, human_admit: bool) -> str:
    if target not in _TRANSITIONS.get(current, set()):
        raise ValueError(f"illegal lifecycle transition: {current} -> {target}")
    if not evidence_present:
        raise ValueError("transition evidence is required")
    if target in {"qualified", "revoked"} and not human_admit:
        raise ValueError("human admit is required")
    return target


@dataclass(frozen=True)
class LifecycleEvidenceReceipt:
    evidence_id: str
    artifact_digest: str
    evaluation_digest: str
    verdict: Literal["pass", "fail"]
    observed_at: datetime
    deterministic_safety_failure: bool = False


@dataclass(frozen=True)
class LifecycleCommand:
    event_id: str
    artifact_digest: str
    target_state: str
    actor: str
    reason: str
    occurred_at: datetime
    human_admit: bool


@dataclass(frozen=True)
class LifecycleEvent:
    event_id: str
    artifact_digest: str
    prior_state: str
    target_state: str
    evidence_id: str
    actor: str
    reason: str
    occurred_at: datetime
    human_admit: bool


def transition_lifecycle(
    history: Sequence[LifecycleEvent],
    command: LifecycleCommand,
    evidence: LifecycleEvidenceReceipt,
) -> LifecycleEvent:
    if not command.event_id or any(item.event_id == command.event_id for item in history):
        raise ValueError("duplicate or empty lifecycle event id")
    if any(item.evidence_id == evidence.evidence_id for item in history):
        raise ValueError("lifecycle evidence cannot be reused")
    if not _is_sha256(command.artifact_digest) or command.artifact_digest != evidence.artifact_digest:
        raise ValueError("lifecycle evidence artifact digest mismatch")
    if not _is_sha256(evidence.evaluation_digest):
        raise ValueError("lifecycle evaluation digest is required")
    if command.occurred_at.tzinfo is None or evidence.observed_at.tzinfo is None:
        raise ValueError("lifecycle timestamps must be timezone-aware")
    if evidence.observed_at > command.occurred_at:
        raise ValueError("lifecycle evidence cannot be observed after the command")
    current = history[-1].target_state if history else "candidate"
    if history:
        if any(item.artifact_digest != command.artifact_digest for item in history):
            raise ValueError("a new artifact digest requires a new candidate lifecycle")
        if command.occurred_at <= history[-1].occurred_at:
            raise ValueError("lifecycle event time must be monotonic")
    if command.target_state not in _TRANSITIONS.get(current, set()):
        raise ValueError(f"illegal lifecycle transition: {current} -> {command.target_state}")
    if evidence.deterministic_safety_failure and command.target_state == "qualified":
        raise ValueError("deterministic safety failure cannot be overridden")
    if command.target_state == "qualified":
        if evidence.verdict != "pass" or not command.human_admit:
            raise ValueError("qualification requires pass evidence and human admit")
        if current == "suspended" and evidence.observed_at <= history[-1].occurred_at:
            raise ValueError("suspended recovery requires fresh evidence")
    if command.target_state == "revoked" and not command.human_admit:
        raise ValueError("revocation requires human admit")
    return LifecycleEvent(
        event_id=command.event_id,
        artifact_digest=command.artifact_digest,
        prior_state=current,
        target_state=command.target_state,
        evidence_id=evidence.evidence_id,
        actor=command.actor,
        reason=command.reason,
        occurred_at=command.occurred_at,
        human_admit=command.human_admit,
    )


@dataclass(frozen=True)
class CaseLifecycleObservation:
    case_digest: str
    model_matrix_digest: str
    discriminative_index_ppm: int
    oracle_current: bool
    exact_duplicate: bool


class CaseLifecycleAssessment(TypedDict):
    recommendation: Literal["reject_exact_duplicate", "reoracle_required", "retain_candidate"]
    automatic_transition_allowed: Literal[False]
    reason: str
    discriminative_index_ppm: int


def assess_test_case_lifecycle(observation: CaseLifecycleObservation) -> CaseLifecycleAssessment:
    if not _is_sha256(observation.case_digest) or not _is_sha256(observation.model_matrix_digest):
        raise ValueError("test-case lifecycle digests are required")
    if not 0 <= observation.discriminative_index_ppm <= 1_000_000:
        raise ValueError("discriminative index must be between 0 and 1,000,000 ppm")
    if observation.exact_duplicate:
        recommendation = "reject_exact_duplicate"
    elif not observation.oracle_current:
        recommendation = "reoracle_required"
    else:
        recommendation = "retain_candidate"
    return {
        "recommendation": recommendation,
        "automatic_transition_allowed": False,
        "reason": "numeric core/archive/remutation thresholds are not calibrated",
        "discriminative_index_ppm": observation.discriminative_index_ppm,
    }


_SECRET = re.compile(r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(token|secret|password)\s*[:=]\s*\S+", re.IGNORECASE)


def sanitize_trace(trace: Mapping[str, JSONValue]) -> dict[str, str]:
    text = json.dumps(trace, ensure_ascii=False)
    if _SECRET.search(text):
        raise TraceRejected("secret-like content detected")
    return {key: str(trace[key]) for key in ("task", "error") if key in trace}


@dataclass(frozen=True)
class TraceEvent:
    event_id: str
    depends_on: tuple[str, ...]
    kind: Literal[
        "user_intent",
        "tool_call",
        "tool_result",
        "failure",
        "system_prompt",
        "reasoning",
    ]
    payload: Mapping[str, JSONValue]


def trace_feedback_digest(
    events: Sequence[TraceEvent],
    failure_event_id: str,
    source_session_id: str,
) -> str:
    payload = {
        "events": [
            {
                "event_id": event.event_id,
                "depends_on": list(event.depends_on),
                "kind": event.kind,
                "payload": dict(event.payload),
            }
            for event in events
        ],
        "failure_event_id": failure_event_id,
        "source_session_hash": "sha256:"
        + hashlib.sha256(source_session_id.encode()).hexdigest(),
    }
    return "sha256:" + hashlib.sha256(canonical_bytes(payload)).hexdigest()


@dataclass(frozen=True)
class TraceExportPolicy:
    capture_authorized: bool
    retention_until: datetime
    sanitizer_version: str
    allow_system_prompt: bool = False
    allow_reasoning: bool = False


@dataclass(frozen=True)
class SanitizedSeed:
    seed_id: str
    source_session_hash: str
    failure_classification: str
    minimal_events: tuple[SanitizedTraceEvent, ...]
    sanitizer_version: str
    execution_receipt_hash: str
    export_receipt_digest: str


_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_TOKEN = re.compile(r"\b(?:sk|ghp|tok)_[A-Za-z0-9_-]{8,}\b")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_SQL_LITERAL = re.compile(r"(?i)(?P<prefix>\b(?:where|and|or)\s+[A-Za-z_][A-Za-z0-9_]*\s*=\s*)'[^']*'")


def _pseudonym(kind: str, value: str) -> str:
    return hashlib.sha256(f"{kind}\0{value}".encode()).hexdigest()[:12]


def _sanitize_string(value: str) -> str:
    sanitized = _EMAIL.sub(lambda match: f"user_{_pseudonym('email', match.group(0))}@example.invalid", value)
    sanitized = _TOKEN.sub(lambda match: f"tok_{_pseudonym('token', match.group(0))}", sanitized)
    sanitized = _ISO_DATE.sub("2000-01-01", sanitized)
    sanitized = _SQL_LITERAL.sub(
        lambda match: f"{match.group('prefix')}'redacted_{_pseudonym('sql', match.group(0))}'",
        sanitized,
    )
    return sanitized


class SanitizedTraceEvent(TypedDict):
    event_id: str
    depends_on: list[str]
    kind: str
    payload: JSONValue


def _sanitize_value(value: object) -> JSONValue:
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise TraceRejected(f"unsupported trace value type: {type(value).__name__}")


def slice_sanitize_normalize(
    events: Sequence[TraceEvent],
    failure_event_id: str,
    policy: TraceExportPolicy,
    *,
    source_session_id: str,
    execution_receipt: object,
    execution_trusted_keys: Mapping[str, bytes],
    expected_authorization_receipt_hash: str,
    expected_decision_hash: str,
    expected_workload_identity_receipt_hash: str,
    expected_execution_attempt_id: str,
    expected_skill_id: str,
    expected_artifact_digest: str,
    expected_qualification_receipt_id: str,
    expected_snapshot_hash: str,
    expected_host_profile_id: str,
    expected_carrier_id: str,
    expected_attestation_evidence_digest: str,
    expected_nonce_consumption_evidence_digest: str,
    expected_execution_audience: str,
    now: datetime | None = None,
    known_seed_digests: set[str] | None = None,
) -> SanitizedSeed:
    observed_now = now or datetime.now(timezone.utc)
    if not policy.capture_authorized:
        raise TraceRejected("trace capture is not authorized")
    if policy.retention_until.tzinfo is None or observed_now.tzinfo is None:
        raise TraceRejected("trace retention timestamps must be timezone-aware")
    if policy.retention_until <= observed_now:
        raise TraceRejected("trace retention has expired")
    if not policy.sanitizer_version:
        raise TraceRejected("sanitizer version is required")
    by_id = {event.event_id: event for event in events}
    if len(by_id) != len(events) or failure_event_id not in by_id:
        raise TraceRejected("duplicate event id or missing failure event")
    if by_id[failure_event_id].kind != "failure":
        raise TraceRejected("failure_event_id must identify a failure")

    selected: set[str] = set()
    visiting: set[str] = set()

    def visit(event_id: str) -> None:
        if event_id in visiting:
            raise TraceRejected("trace dependency cycle")
        if event_id in selected:
            return
        event = by_id.get(event_id)
        if event is None:
            raise TraceRejected(f"missing trace dependency: {event_id}")
        visiting.add(event_id)
        for dependency in event.depends_on:
            visit(dependency)
        visiting.remove(event_id)
        selected.add(event_id)

    visit(failure_event_id)
    selected.update(event.event_id for event in events if event.kind == "user_intent")
    ordered = [event for event in events if event.event_id in selected]
    excluded_kinds: set[str] = set()
    if not policy.allow_system_prompt:
        excluded_kinds.add("system_prompt")
    if not policy.allow_reasoning:
        excluded_kinds.add("reasoning")

    def exported_dependencies(event: TraceEvent) -> list[str]:
        flattened: list[str] = []
        for dependency_id in event.depends_on:
            dependency = by_id[dependency_id]
            if dependency.kind in excluded_kinds:
                flattened.extend(exported_dependencies(dependency))
            else:
                flattened.append(dependency_id)
        return list(dict.fromkeys(flattened))

    minimal_events: list[SanitizedTraceEvent] = []
    for event in ordered:
        if event.kind in excluded_kinds:
            continue
        minimal_events.append(
            {
                "event_id": event.event_id,
                "depends_on": exported_dependencies(event),
                "kind": event.kind,
                "payload": _sanitize_value(event.payload),
            }
        )
    serialized = json.dumps(minimal_events, ensure_ascii=False, sort_keys=True)
    if _SECRET.search(serialized):
        raise TraceRejected("secret-like content remained after sanitization")
    lowered = serialized.lower()
    if '"reasoning"' in lowered or '"system_prompt"' in lowered:
        raise TraceRejected("reasoning or system prompt escaped the export policy")
    feedback_digest = trace_feedback_digest(
        events,
        failure_event_id,
        source_session_id,
    )
    try:
        execution_feedback = verify_execution_feedback_receipt(
            execution_receipt,
            execution_trusted_keys,
            expected_authorization_receipt_hash=(
                expected_authorization_receipt_hash
            ),
            expected_decision_hash=expected_decision_hash,
            expected_workload_identity_receipt_hash=(
                expected_workload_identity_receipt_hash
            ),
            expected_execution_attempt_id=expected_execution_attempt_id,
            expected_skill_id=expected_skill_id,
            expected_artifact_digest=expected_artifact_digest,
            expected_qualification_receipt_id=expected_qualification_receipt_id,
            expected_snapshot_hash=expected_snapshot_hash,
            expected_host_profile_id=expected_host_profile_id,
            expected_carrier_id=expected_carrier_id,
            expected_attestation_evidence_digest=(
                expected_attestation_evidence_digest
            ),
            expected_nonce_consumption_evidence_digest=(
                expected_nonce_consumption_evidence_digest
            ),
            expected_feedback_digest=feedback_digest,
            expected_outcome="failed",
            expected_audience=expected_execution_audience,
            now=observed_now,
        )
    except EvidenceRejected as exc:
        raise TraceRejected(f"execution feedback rejected: {exc}") from exc
    classification = "execution_failure"
    failure_text = json.dumps(by_id[failure_event_id].payload, ensure_ascii=False).lower()
    if "syntax" in failure_text:
        classification = "syntax_failure"
    elif "timeout" in failure_text:
        classification = "timeout_failure"
    source_hash = "sha256:" + hashlib.sha256(source_session_id.encode()).hexdigest()
    payload = {
        "source_session_hash": source_hash,
        "failure_classification": classification,
        "minimal_events": minimal_events,
        "sanitizer_version": policy.sanitizer_version,
        "execution_receipt_hash": execution_feedback.receipt_hash,
    }
    payload_digest = "sha256:" + hashlib.sha256(canonical_bytes(payload)).hexdigest()
    if known_seed_digests is not None and payload_digest in known_seed_digests:
        raise TraceRejected("duplicate sanitized seed")
    if known_seed_digests is not None:
        known_seed_digests.add(payload_digest)
    return SanitizedSeed(
        seed_id="seed-" + payload_digest.removeprefix("sha256:")[:16],
        source_session_hash=source_hash,
        failure_classification=classification,
        minimal_events=tuple(minimal_events),
        sanitizer_version=policy.sanitizer_version,
        execution_receipt_hash=execution_feedback.receipt_hash,
        export_receipt_digest=payload_digest,
    )
