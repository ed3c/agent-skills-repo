"""Credential-free replay and tamper detection for Arena experiment bundles."""
from __future__ import annotations

import base64
import binascii
import json
import stat
from pathlib import Path
from typing import Mapping, cast

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .model import (
    BUNDLE_MANIFEST_SCHEMA,
    BUNDLE_SIGNATURE_DOMAIN,
    FAILURE_DENOMINATOR_POLICY,
    INVOCATION_MANIFEST_SCHEMA,
    METRICS_SCHEMA,
    OUTCOME_CLASSES,
    OUTCOME_SCHEMA,
    REQUIRED_EVIDENCE_FILES,
    RUN_INDEX_SCHEMA,
    SIGNED_BUNDLE_SCHEMA,
    TRAJECTORY_SCHEMA,
    VERIFIER_SCHEMA,
    WORKSPACE_POLICY,
    ExperimentError,
    _METRICS_FIELDS,
    canonical_bytes,
    identifier,
    regular_file,
    require_sha256,
    sha256_bytes,
    sha256_json,
    timestamp,
    validate_relative_artifact_path,
)
from .plan import verify_plan_envelope

_BUNDLE_ENVELOPE_FIELDS = {
    "schema_version",
    "payload",
    "manifest_hash",
    "issuer_key_id",
    "signature",
}
_BUNDLE_MANIFEST_FIELDS = {
    "schema_version",
    "experiment_id",
    "plan_digest",
    "plan_hash",
    "plan_envelope_digest",
    "run_index_digest",
    "planned_invocation_count",
    "recorded_invocation_count",
    "invocation_manifests",
    "failure_denominator_policy",
    "issued_at",
}
_RUN_INDEX_FIELDS = {
    "schema_version",
    "plan_digest",
    "failure_denominator_policy",
    "planned_invocation_count",
    "recorded_invocation_count",
    "execution_sequence",
    "records",
    "outcome_counts",
    "run_index_digest",
}
_RECORD_FIELDS = {"invocation_id", "manifest_digest", "classification"}
_INVOCATION_MANIFEST_FIELDS = {
    "schema_version",
    "invocation_id",
    "classification",
    "files",
    "manifest_digest",
}
_FILE_ENTRY_FIELDS = {"path", "sha256", "size_bytes"}
_OUTCOME_FIELDS = {
    "schema_version",
    "invocation_id",
    "classification",
    "reward",
    "adapter_exit_code",
    "error_code",
    "started_at",
    "completed_at",
    "workspace_nonce",
    "workspace_policy",
    "cleanup_verified",
    "attempt_number",
    "retry_count",
    "artifact_file_count",
}
_TRAJECTORY_FIELDS = {"schema_version", "events"}
_VERIFIER_FIELDS = {"schema_version", "status", "reward", "diagnostics_digest"}


def _load_object(path: Path, label: str) -> dict[str, object]:
    data = regular_file(path, label)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ExperimentError(f"{label} root must be an object")
    return value


def _verify_bundle_envelope(
    envelope: Mapping[str, object],
    trusted_keys: Mapping[str, bytes],
) -> dict[str, object]:
    if (
        set(envelope) != _BUNDLE_ENVELOPE_FIELDS
        or envelope.get("schema_version") != SIGNED_BUNDLE_SCHEMA
    ):
        raise ExperimentError("signed bundle envelope fields are invalid")
    payload = envelope.get("payload")
    if not isinstance(payload, Mapping):
        raise ExperimentError("signed bundle payload is absent")
    if (
        set(payload) != _BUNDLE_MANIFEST_FIELDS
        or payload.get("schema_version") != BUNDLE_MANIFEST_SCHEMA
    ):
        raise ExperimentError("bundle manifest schema or fields are invalid")
    raw = canonical_bytes(payload)
    if envelope.get("manifest_hash") != sha256_bytes(raw):
        raise ExperimentError("signed bundle manifest hash mismatch")
    key_id = identifier(envelope.get("issuer_key_id"), "bundle issuer_key_id")
    key = trusted_keys.get(key_id)
    if not isinstance(key, bytes) or len(key) != 32:
        raise ExperimentError("signed bundle issuer is not trusted")
    signature_value = envelope.get("signature")
    if not isinstance(signature_value, str):
        raise ExperimentError("signed bundle signature is invalid")
    try:
        signature = base64.b64decode(signature_value, validate=True)
        Ed25519PublicKey.from_public_bytes(key).verify(
            signature,
            BUNDLE_SIGNATURE_DOMAIN + raw,
        )
    except (InvalidSignature, ValueError, TypeError, binascii.Error) as exc:
        raise ExperimentError("signed bundle signature is invalid") from exc
    return dict(payload)


def _validate_metrics(value: Mapping[str, object]) -> None:
    if set(value) != _METRICS_FIELDS or value.get("schema_version") != METRICS_SCHEMA:
        raise ExperimentError("replayed metrics schema or fields are invalid")
    for field in _METRICS_FIELDS - {"schema_version"}:
        if type(value[field]) is not int or cast(int, value[field]) < 0:
            raise ExperimentError(f"replayed metric {field} is invalid")


def _validate_trajectory(value: Mapping[str, object]) -> None:
    if set(value) != _TRAJECTORY_FIELDS or value.get("schema_version") != TRAJECTORY_SCHEMA:
        raise ExperimentError("replayed trajectory schema or fields are invalid")
    events = value.get("events")
    if not isinstance(events, list) or not all(isinstance(item, dict) for item in events):
        raise ExperimentError("replayed trajectory events are invalid")


def _validate_verifier(value: Mapping[str, object], reward: object) -> None:
    if set(value) != _VERIFIER_FIELDS or value.get("schema_version") != VERIFIER_SCHEMA:
        raise ExperimentError("replayed verifier schema or fields are invalid")
    if not isinstance(value.get("status"), str) or not value["status"]:
        raise ExperimentError("replayed verifier status is invalid")
    if value.get("reward") != reward:
        raise ExperimentError("replayed verifier reward differs from outcome")
    diagnostics = value.get("diagnostics_digest")
    if diagnostics is not None:
        require_sha256(diagnostics, "replayed verifier diagnostics_digest")


def _actual_files(root: Path) -> set[str]:
    if root.is_symlink() or not root.is_dir():
        raise ExperimentError(f"invocation directory is absent or unsafe: {root}")
    files: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ExperimentError(f"experiment bundle contains symlink: {relative}")
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ExperimentError(f"experiment bundle contains special file: {relative}")
        files.add(relative)
    return files


def _verify_invocation(
    invocation_root: Path,
    expected_invocation: Mapping[str, object],
    record: Mapping[str, object],
    seen_nonces: set[str],
) -> str:
    invocation_id = cast(str, expected_invocation["invocation_id"])
    if set(record) != _RECORD_FIELDS or record.get("invocation_id") != invocation_id:
        raise ExperimentError("run-index invocation record is invalid or reordered")
    classification = record.get("classification")
    if classification not in OUTCOME_CLASSES:
        raise ExperimentError("run-index classification is invalid")
    require_sha256(record.get("manifest_digest"), "run-index manifest_digest")

    manifest = _load_object(
        invocation_root / "invocation-manifest.json",
        f"invocation {invocation_id} manifest",
    )
    if (
        set(manifest) != _INVOCATION_MANIFEST_FIELDS
        or manifest.get("schema_version") != INVOCATION_MANIFEST_SCHEMA
        or manifest.get("invocation_id") != invocation_id
        or manifest.get("classification") != classification
    ):
        raise ExperimentError("invocation manifest identity is invalid")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ExperimentError("invocation manifest has no files")
    paths: list[str] = []
    for entry in files:
        if not isinstance(entry, Mapping) or set(entry) != _FILE_ENTRY_FIELDS:
            raise ExperimentError("invocation manifest file entry is invalid")
        path = entry.get("path")
        if not isinstance(path, str):
            raise ExperimentError("invocation manifest file path is invalid")
        if path.startswith("artifacts/"):
            validate_relative_artifact_path(path.removeprefix("artifacts/"))
        elif path not in REQUIRED_EVIDENCE_FILES:
            raise ExperimentError(f"invocation manifest contains unknown evidence path: {path}")
        require_sha256(entry.get("sha256"), f"invocation file {path} digest")
        size = entry.get("size_bytes")
        if type(size) is not int or size < 0:
            raise ExperimentError(f"invocation file {path} size is invalid")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ExperimentError("invocation manifest paths must be sorted and unique")
    if not set(REQUIRED_EVIDENCE_FILES) <= set(paths):
        raise ExperimentError("invocation manifest is missing required evidence")
    expected_files = set(paths) | {"invocation-manifest.json"}
    if _actual_files(invocation_root) != expected_files:
        raise ExperimentError("invocation directory file set differs from manifest")
    for entry in files:
        path = cast(str, entry["path"])
        data = regular_file(invocation_root / path, f"invocation file {path}")
        if entry["sha256"] != sha256_bytes(data) or entry["size_bytes"] != len(data):
            raise ExperimentError(f"invocation file {path} digest or size mismatch")
    manifest_without_digest = {
        key: value for key, value in manifest.items() if key != "manifest_digest"
    }
    expected_manifest_digest = sha256_json(manifest_without_digest)
    if manifest.get("manifest_digest") != expected_manifest_digest:
        raise ExperimentError("invocation manifest digest mismatch")
    if record.get("manifest_digest") != expected_manifest_digest:
        raise ExperimentError("run-index does not bind invocation manifest")

    invocation = _load_object(
        invocation_root / "invocation.json",
        f"invocation {invocation_id} identity",
    )
    if invocation != dict(expected_invocation):
        raise ExperimentError("recorded invocation differs from preregistered plan")
    outcome = _load_object(
        invocation_root / "outcome.json",
        f"invocation {invocation_id} outcome",
    )
    if (
        set(outcome) != _OUTCOME_FIELDS
        or outcome.get("schema_version") != OUTCOME_SCHEMA
        or outcome.get("invocation_id") != invocation_id
        or outcome.get("classification") != classification
        or outcome.get("workspace_policy") != WORKSPACE_POLICY
        or outcome.get("cleanup_verified") is not True
        or outcome.get("attempt_number") != 1
        or outcome.get("retry_count") != 0
    ):
        raise ExperimentError("invocation outcome identity or retry policy is invalid")
    timestamp(outcome.get("started_at"), "replayed outcome started_at")
    timestamp(outcome.get("completed_at"), "replayed outcome completed_at")
    nonce = identifier(outcome.get("workspace_nonce"), "workspace_nonce")
    if nonce in seen_nonces:
        raise ExperimentError("workspace nonce was reused across invocations")
    seen_nonces.add(nonce)
    artifact_count = outcome.get("artifact_file_count")
    if type(artifact_count) is not int or artifact_count < 0:
        raise ExperimentError("artifact_file_count is invalid")
    if artifact_count != sum(path.startswith("artifacts/") for path in paths):
        raise ExperimentError("artifact_file_count differs from invocation manifest")

    trajectory = _load_object(
        invocation_root / "trajectory.json",
        f"invocation {invocation_id} trajectory",
    )
    verifier = _load_object(
        invocation_root / "verifier.json",
        f"invocation {invocation_id} verifier",
    )
    metrics = _load_object(
        invocation_root / "metrics.json",
        f"invocation {invocation_id} metrics",
    )
    _validate_trajectory(trajectory)
    _validate_verifier(verifier, outcome.get("reward"))
    _validate_metrics(metrics)
    return cast(str, classification)


def replay_bundle(
    bundle_dir: Path | str,
    *,
    trusted_plan_keys: Mapping[str, bytes],
    trusted_bundle_keys: Mapping[str, bytes],
) -> dict[str, object]:
    """Verify a bundle without model credentials or private signing keys."""
    root = Path(bundle_dir)
    if root.is_symlink() or not root.is_dir():
        raise ExperimentError(f"experiment bundle directory is absent: {root}")
    bundle_envelope = _load_object(
        root / "bundle-envelope.json",
        "bundle envelope",
    )
    payload = _verify_bundle_envelope(bundle_envelope, trusted_bundle_keys)
    manifest_hash = cast(str, bundle_envelope["manifest_hash"])
    if root.name != "sha256-" + manifest_hash.removeprefix("sha256:"):
        raise ExperimentError("content-addressed bundle directory name is invalid")
    manifest_file = _load_object(root / "bundle-manifest.json", "bundle manifest")
    if manifest_file != payload:
        raise ExperimentError("bundle manifest file differs from signed payload")
    timestamp(payload.get("issued_at"), "bundle issued_at")
    if payload.get("failure_denominator_policy") != FAILURE_DENOMINATOR_POLICY:
        raise ExperimentError("bundle failure denominator policy is invalid")

    plan_envelope = _load_object(root / "plan-envelope.json", "plan envelope")
    plan = verify_plan_envelope(plan_envelope, trusted_plan_keys)
    if payload.get("plan_envelope_digest") != sha256_json(plan_envelope):
        raise ExperimentError("bundle does not bind the signed plan envelope")
    if payload.get("plan_hash") != plan_envelope.get("plan_hash"):
        raise ExperimentError("bundle plan hash differs from signed plan")
    if payload.get("plan_digest") != plan.get("plan_digest"):
        raise ExperimentError("bundle plan digest differs from signed plan")

    run_index = _load_object(root / "run-index.json", "run index")
    if set(run_index) != _RUN_INDEX_FIELDS or run_index.get("schema_version") != RUN_INDEX_SCHEMA:
        raise ExperimentError("run-index schema or fields are invalid")
    run_index_without_digest = {
        key: value for key, value in run_index.items() if key != "run_index_digest"
    }
    if run_index.get("run_index_digest") != sha256_json(run_index_without_digest):
        raise ExperimentError("run-index digest mismatch")
    if payload.get("run_index_digest") != run_index.get("run_index_digest"):
        raise ExperimentError("bundle does not bind the run index")
    if run_index.get("plan_digest") != plan.get("plan_digest"):
        raise ExperimentError("run-index plan digest mismatch")
    if run_index.get("failure_denominator_policy") != FAILURE_DENOMINATOR_POLICY:
        raise ExperimentError("run-index denominator policy is invalid")

    plan_invocations = cast(list[dict[str, object]], plan["invocations"])
    expected_sequence = [cast(str, item["invocation_id"]) for item in plan_invocations]
    if run_index.get("execution_sequence") != expected_sequence:
        raise ExperimentError("execution sequence differs from preregistered plan")
    planned = plan["planned_invocation_count"]
    records = run_index.get("records")
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise ExperimentError("run-index records are invalid")
    if (
        run_index.get("planned_invocation_count") != planned
        or run_index.get("recorded_invocation_count") != planned
        or len(records) != planned
        or payload.get("planned_invocation_count") != planned
        or payload.get("recorded_invocation_count") != planned
        or payload.get("invocation_manifests") != records
    ):
        raise ExperimentError("preregistered invocation denominator is incomplete")

    invocations_root = root / "invocations"
    if invocations_root.is_symlink() or not invocations_root.is_dir():
        raise ExperimentError("bundle invocations directory is absent")
    actual_invocation_dirs = sorted(
        path.name for path in invocations_root.iterdir() if path.is_dir()
    )
    if actual_invocation_dirs != sorted(expected_sequence):
        raise ExperimentError("bundle invocation directory set is incomplete or expanded")
    if any(path.is_symlink() or not path.is_dir() for path in invocations_root.iterdir()):
        raise ExperimentError("bundle invocations root contains a non-directory entry")

    seen_nonces: set[str] = set()
    observed_counts = {name: 0 for name in sorted(OUTCOME_CLASSES)}
    for expected_invocation, record in zip(plan_invocations, records, strict=True):
        invocation_id = cast(str, expected_invocation["invocation_id"])
        classification = _verify_invocation(
            invocations_root / invocation_id,
            expected_invocation,
            cast(Mapping[str, object], record),
            seen_nonces,
        )
        observed_counts[classification] += 1
    if run_index.get("outcome_counts") != observed_counts:
        raise ExperimentError("run-index outcome counts differ from invocation evidence")

    top_level_files = {path.name for path in root.iterdir() if path.is_file()}
    if top_level_files != {
        "bundle-envelope.json",
        "bundle-manifest.json",
        "plan-envelope.json",
        "run-index.json",
    }:
        raise ExperimentError("bundle top-level file set is invalid")
    if any(path.is_symlink() for path in root.iterdir()):
        raise ExperimentError("bundle top level contains a symlink")
    if {path.name for path in root.iterdir() if path.is_dir()} != {"invocations"}:
        raise ExperimentError("bundle top-level directory set is invalid")

    return {
        "status": "verified",
        "experiment_id": payload["experiment_id"],
        "manifest_hash": manifest_hash,
        "plan_digest": plan["plan_digest"],
        "recorded_invocation_count": planned,
        "outcome_counts": observed_counts,
    }


__all__ = ["replay_bundle"]
