"""Offline admission and provenance checks for two physical sandbox bundles.

This module never starts OpenShell. It consumes two already-produced evidence
bundles and fails closed unless they are independently admissible, share the
same pinned execution envelope, carry distinct sandbox/workspace identities,
and preserve deterministic case evidence. It also performs a zero-print scan
for the external development private key across every Git object and the
current worktree.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, Sequence, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from skill_arena.core import EvidenceRejected, verify_sandbox_case_receipts
from skill_arena.sandbox_executor.model import (
    BUNDLE_SCHEMA,
    SandboxCase,
    SandboxProfile,
    load_json_object,
    sha256_bytes,
    sha256_json,
)
from skill_arena.sandbox_executor.signing import load_private_key

PAIR_SCHEMA = "openshell-physical-evidence-pair@1"
_EXPECTED_BUNDLE_FILES = (
    "attestation.json",
    "receipt.json",
    "result.json",
)


class EvidencePairError(ValueError):
    """Two physical evidence bundles cannot be admitted as one pair."""


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise EvidencePairError(f"{label} must be a regular non-symlink file: {path}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise EvidencePairError(f"cannot read {label}: {path}: {exc}") from exc


def load_public_key(path: Path | str) -> Ed25519PublicKey:
    source = Path(path)
    data = _regular_file(source, "public key")
    try:
        if data.startswith(b"-----BEGIN"):
            loaded = serialization.load_pem_public_key(data)
            if not isinstance(loaded, Ed25519PublicKey):
                raise TypeError("not Ed25519")
            return loaded
        if len(data) == 32:
            return Ed25519PublicKey.from_public_bytes(data)
    except (TypeError, ValueError) as exc:
        raise EvidencePairError("public key is not valid Ed25519 material") from exc
    raise EvidencePairError(
        "public key must be 32 raw Ed25519 bytes or a PEM public key"
    )


def _load_bundle(bundle_dir: Path) -> dict[str, object]:
    root = bundle_dir.resolve()
    if root.is_symlink() or not root.is_dir():
        raise EvidencePairError(f"bundle directory is absent: {bundle_dir}")
    manifest_path = root / "bundle-manifest.json"
    manifest = load_json_object(manifest_path)
    if set(manifest) != {"schema_version", "files", "bundle_digest"}:
        raise EvidencePairError(f"bundle manifest fields are invalid: {root}")
    if manifest.get("schema_version") != BUNDLE_SCHEMA:
        raise EvidencePairError(f"bundle manifest schema is invalid: {root}")
    raw_entries = manifest.get("files")
    if not isinstance(raw_entries, list):
        raise EvidencePairError(f"bundle manifest files must be a list: {root}")
    entries: list[dict[str, str]] = []
    for index, raw in enumerate(raw_entries):
        if not isinstance(raw, dict) or set(raw) != {"path", "sha256"}:
            raise EvidencePairError(f"bundle manifest file {index} is malformed: {root}")
        relative = raw.get("path")
        digest = raw.get("sha256")
        if not isinstance(relative, str) or relative not in _EXPECTED_BUNDLE_FILES:
            raise EvidencePairError(
                f"bundle manifest has an unexpected path: {relative!r}"
            )
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise EvidencePairError(
                f"bundle manifest digest is invalid for {relative!r}"
            )
        data = _regular_file(root / relative, f"bundle file {relative}")
        actual = hashlib.sha256(data).hexdigest()
        if actual != digest:
            raise EvidencePairError(
                f"bundle file digest mismatch for {relative}: {actual} != {digest}"
            )
        entries.append({"path": relative, "sha256": digest})
    if entries != sorted(entries, key=lambda item: item["path"]):
        raise EvidencePairError("bundle manifest paths must be sorted")
    if tuple(item["path"] for item in entries) != _EXPECTED_BUNDLE_FILES:
        raise EvidencePairError(
            "bundle manifest must bind exactly attestation, receipt, and result"
        )
    expected_bundle_digest = sha256_json({"files": entries})
    if manifest.get("bundle_digest") != expected_bundle_digest:
        raise EvidencePairError("bundle manifest bundle_digest mismatch")

    receipt = load_json_object(root / "receipt.json")
    attestation = load_json_object(root / "attestation.json")
    result = load_json_object(root / "result.json")
    payload = receipt.get("payload")
    if not isinstance(payload, Mapping):
        raise EvidencePairError("receipt payload is absent")
    if payload.get("sandbox_attestation_evidence_digest") != sha256_json(attestation):
        raise EvidencePairError("receipt is not bound to attestation.json")
    if result.get("workspace_nonce") != attestation.get("workspace_nonce"):
        raise EvidencePairError("result and attestation workspace nonce differ")
    if payload.get("sandbox_image_digest") != attestation.get("sandbox_image_digest"):
        raise EvidencePairError("receipt and attestation image digest differ")
    if payload.get("sandbox_policy_digest") != attestation.get("sandbox_policy_digest"):
        raise EvidencePairError("receipt and attestation policy digest differ")
    if attestation.get("cleanup_verified") is not True:
        raise EvidencePairError("sandbox cleanup is not verified")
    if attestation.get("workspace_destroyed") is not True:
        raise EvidencePairError("sandbox workspace destruction is not verified")

    return {
        "root": root,
        "manifest": manifest,
        "receipt": receipt,
        "attestation": attestation,
        "result": result,
        "file_hashes": {
            name: sha256_bytes(_regular_file(root / name, f"bundle file {name}"))
            for name in (*_EXPECTED_BUNDLE_FILES, "bundle-manifest.json")
        },
    }


def _issued_time(receipt: Mapping[str, object]) -> datetime:
    payload = receipt.get("payload")
    if not isinstance(payload, Mapping):
        raise EvidencePairError("receipt payload is absent")
    try:
        value = datetime.fromisoformat(cast(str, payload["issued_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidencePairError("receipt issued_at is invalid") from exc
    if value.tzinfo is None:
        raise EvidencePairError("receipt issued_at must include timezone")
    return value.astimezone(timezone.utc)


def _expires_time(receipt: Mapping[str, object]) -> datetime:
    payload = receipt.get("payload")
    if not isinstance(payload, Mapping):
        raise EvidencePairError("receipt payload is absent")
    try:
        value = datetime.fromisoformat(cast(str, payload["expires_at"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidencePairError("receipt expires_at is invalid") from exc
    if value.tzinfo is None:
        raise EvidencePairError("receipt expires_at must include timezone")
    return value.astimezone(timezone.utc)


def _admit_receipt(
    receipt: Mapping[str, object],
    *,
    public_key: Ed25519PublicKey,
    issuer_key_id: str,
    case: SandboxCase,
    profile: SandboxProfile,
    benchmark_suite_digest: str,
    skill_artifact_digest: str,
    image_digest: str,
    policy_digest: str,
) -> None:
    verification_time = _issued_time(receipt) + timedelta(microseconds=1)
    verify_sandbox_case_receipts(
        [receipt],
        {issuer_key_id: public_key.public_bytes_raw()},
        expected_cases=[case.arena_case],
        expected_benchmark_suite_digest=benchmark_suite_digest,
        expected_skill_artifact_digest=skill_artifact_digest,
        expected_target_host_profile_id=profile.target_host_profile_id,
        expected_target_host_version=profile.target_host_version,
        expected_target_transport_profile=profile.target_transport_profile,
        expected_target_policy_profile=profile.target_policy_profile,
        expected_sandbox_profile_id=profile.sandbox_profile_id,
        expected_sandbox_image_digest=image_digest,
        expected_sandbox_policy_digest=policy_digest,
        expected_artifact_access_policy=profile.artifact_access_policy,
        expected_workspace_disposition=profile.workspace_disposition,
        expected_secret_policy=profile.secret_policy,
        expected_allowed_tools_digest=profile.allowed_tools_digest,
        expected_cpu_time_ms=profile.resource_limits.cpu_time_ms,
        expected_wall_time_ms=profile.resource_limits.wall_time_ms,
        expected_memory_bytes=profile.resource_limits.memory_bytes,
        expected_process_count_max=profile.resource_limits.process_count_max,
        expected_network_policy=profile.resource_limits.network_policy,
        now=verification_time,
    )


def _tamper_rejected(
    receipt: Mapping[str, object],
    **admission: object,
) -> bool:
    changed = copy.deepcopy(dict(receipt))
    payload = changed.get("payload")
    if not isinstance(payload, dict):
        raise EvidencePairError("receipt payload is absent")
    payload["output_evidence_digest"] = "sha256:" + "0" * 64
    try:
        _admit_receipt(changed, **cast(dict[str, object], admission))
    except EvidenceRejected:
        return True
    raise EvidencePairError("tampered receipt was admitted")


def _git(root: Path, *args: str, input_data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_data,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()
        raise EvidencePairError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


def _secret_representations(private_key_path: Path) -> tuple[bytes, ...]:
    source = _regular_file(private_key_path, "private key")
    try:
        if source.startswith(b"-----BEGIN"):
            loaded = serialization.load_pem_private_key(source, password=None)
            if not isinstance(loaded, Ed25519PrivateKey):
                raise TypeError("not Ed25519")
            raw = loaded.private_bytes_raw()
        elif len(source) == 32:
            raw = source
        else:
            raise ValueError("unexpected key length")
    except (TypeError, ValueError) as exc:
        raise EvidencePairError("private key is not valid Ed25519 material") from exc
    values = {
        raw,
        raw.hex().encode("ascii"),
        base64.b64encode(raw),
    }
    if source.startswith(b"-----BEGIN"):
        values.add(source.strip())
    return tuple(sorted(values, key=lambda value: (len(value), value)))


def _contains_secret(data: bytes, secrets: Sequence[bytes]) -> bool:
    return any(secret and secret in data for secret in secrets)


def audit_private_key_absent(
    repo_root: Path | str,
    private_key_path: Path | str,
) -> dict[str, object]:
    repository = Path(repo_root).resolve()
    key_path = Path(private_key_path).expanduser().resolve()
    if key_path == repository or repository in key_path.parents:
        raise EvidencePairError("private key must live outside the repository")
    if _git(repository, "rev-parse", "--show-toplevel").decode().strip() != str(
        repository
    ):
        raise EvidencePairError("repo_root is not the repository top level")
    secrets = _secret_representations(key_path)

    object_lines = _git(repository, "rev-list", "--objects", "--all").splitlines()
    object_ids = sorted({line.split(maxsplit=1)[0].decode("ascii") for line in object_lines})
    process = subprocess.Popen(
        ["git", "-C", str(repository), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None and process.stdout is not None
    scanned_blobs = 0
    try:
        for object_id in object_ids:
            process.stdin.write(object_id.encode("ascii") + b"\n")
            process.stdin.flush()
            header = process.stdout.readline()
            parts = header.rstrip(b"\n").split()
            if len(parts) == 2 and parts[1] == b"missing":
                raise EvidencePairError(f"git object is missing: {object_id}")
            if len(parts) != 3:
                raise EvidencePairError("git cat-file returned an invalid header")
            object_type = parts[1]
            try:
                size = int(parts[2])
            except ValueError as exc:
                raise EvidencePairError("git cat-file returned an invalid size") from exc
            data = process.stdout.read(size)
            delimiter = process.stdout.read(1)
            if len(data) != size or delimiter != b"\n":
                raise EvidencePairError("git cat-file returned a truncated object")
            if object_type == b"blob":
                scanned_blobs += 1
                if _contains_secret(data, secrets):
                    raise EvidencePairError(
                        f"private key material appears in git blob {object_id}"
                    )
    finally:
        process.stdin.close()
        process.wait(timeout=30)
    if process.returncode != 0:
        stderr = (process.stderr.read() if process.stderr else b"").decode(
            "utf-8", errors="replace"
        )
        raise EvidencePairError(f"git cat-file failed: {stderr.strip()}")

    worktree_paths = _git(
        repository,
        "ls-files",
        "-co",
        "--exclude-standard",
        "-z",
    ).split(b"\0")
    scanned_worktree_files = 0
    for raw_path in worktree_paths:
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        path = repository / relative
        if path.is_symlink() or not path.is_file():
            continue
        scanned_worktree_files += 1
        if _contains_secret(path.read_bytes(), secrets):
            raise EvidencePairError(
                f"private key material appears in worktree file {relative}"
            )
    return {
        "private_key_history_absent": True,
        "scanned_git_blobs": scanned_blobs,
        "scanned_worktree_files": scanned_worktree_files,
    }


def verify_evidence_pair(
    bundle_dirs: Sequence[Path | str],
    *,
    case: SandboxCase,
    profile: SandboxProfile,
    public_key: Ed25519PublicKey,
    private_key_path: Path | str,
    issuer_key_id: str,
    benchmark_suite_digest: str,
    skill_artifact_digest: str,
    repo_root: Path | str,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    if len(bundle_dirs) != 2:
        raise EvidencePairError("exactly two physical bundles are required")
    if not issuer_key_id.startswith("dev-"):
        raise EvidencePairError("issuer_key_id must start with dev-")

    private_key = load_private_key(
        private_key_path,
        repo_root=repo_root,
        issuer_key_id=issuer_key_id,
    )
    private_public = private_key.public_key().public_bytes_raw()
    trusted_public = public_key.public_bytes_raw()
    if private_public != trusted_public:
        raise EvidencePairError("public key does not match the audited private key")

    bundles = [_load_bundle(Path(value)) for value in bundle_dirs]
    receipts = [cast(Mapping[str, object], value["receipt"]) for value in bundles]
    attestations = [cast(Mapping[str, object], value["attestation"]) for value in bundles]
    results = [cast(Mapping[str, object], value["result"]) for value in bundles]
    payloads = [cast(Mapping[str, object], receipt["payload"]) for receipt in receipts]

    issuer_ids = {receipt.get("issuer_key_id") for receipt in receipts}
    if issuer_ids != {issuer_key_id}:
        raise EvidencePairError("receipt issuer_key_id differs from expected trust identity")
    image_digests = {cast(str, payload["sandbox_image_digest"]) for payload in payloads}
    policy_digests = {cast(str, payload["sandbox_policy_digest"]) for payload in payloads}
    if len(image_digests) != 1 or len(policy_digests) != 1:
        raise EvidencePairError("the two runs do not share one image and policy identity")
    image_digest = next(iter(image_digests))
    policy_digest = next(iter(policy_digests))

    admission = {
        "public_key": public_key,
        "issuer_key_id": issuer_key_id,
        "case": case,
        "profile": profile,
        "benchmark_suite_digest": benchmark_suite_digest,
        "skill_artifact_digest": skill_artifact_digest,
        "image_digest": image_digest,
        "policy_digest": policy_digest,
    }
    for receipt in receipts:
        _admit_receipt(receipt, **admission)

    original_hashes = [copy.deepcopy(cast(dict[str, str], value["file_hashes"])) for value in bundles]
    tamper_controls = [
        _tamper_rejected(receipt, **admission) for receipt in receipts
    ]
    for index, bundle in enumerate(bundles):
        current = {
            name: sha256_bytes(
                _regular_file(
                    cast(Path, bundle["root"]) / name,
                    f"bundle file {name}",
                )
            )
            for name in (*_EXPECTED_BUNDLE_FILES, "bundle-manifest.json")
        }
        if current != original_hashes[index]:
            raise EvidencePairError("tamper control mutated an original bundle")

    sandbox_names = {attestation.get("sandbox_name") for attestation in attestations}
    workspace_nonces = {attestation.get("workspace_nonce") for attestation in attestations}
    receipt_ids = {payload.get("receipt_id") for payload in payloads}
    if len(sandbox_names) != 2:
        raise EvidencePairError("physical runs reused a sandbox name")
    if len(workspace_nonces) != 2:
        raise EvidencePairError("physical runs reused a workspace nonce")
    if len(receipt_ids) != 2:
        raise EvidencePairError("physical runs reused a receipt id")

    deterministic_fields = (
        "command_digest",
        "exit_code",
        "stdout_sha256",
        "stderr_sha256",
        "timed_out",
    )
    for field in deterministic_fields:
        if results[0].get(field) != results[1].get(field):
            raise EvidencePairError(f"physical result differs across runs: {field}")
    if payloads[0].get("output_evidence_digest") != payloads[1].get(
        "output_evidence_digest"
    ):
        raise EvidencePairError("receipt output evidence differs across runs")
    if payloads[0].get("output_evidence_digest") != case.expected_evidence_digest:
        raise EvidencePairError("physical result differs from preregistered case evidence")

    history = audit_private_key_absent(repo_root, private_key_path)
    now = (generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    runs: list[dict[str, object]] = []
    for bundle, receipt, attestation, result, payload in zip(
        bundles, receipts, attestations, results, payloads
    ):
        runs.append(
            {
                "bundle_path": str(cast(Path, bundle["root"])),
                "bundle_digest": cast(Mapping[str, object], bundle["manifest"])[
                    "bundle_digest"
                ],
                "receipt_hash": receipt["receipt_hash"],
                "receipt_id": payload["receipt_id"],
                "sandbox_name": attestation["sandbox_name"],
                "workspace_nonce": attestation["workspace_nonce"],
                "sandbox_image_digest": payload["sandbox_image_digest"],
                "sandbox_policy_digest": payload["sandbox_policy_digest"],
                "output_evidence_digest": payload["output_evidence_digest"],
                "issued_at": payload["issued_at"],
                "expires_at": payload["expires_at"],
                "currently_unexpired": now <= _expires_time(receipt),
                "file_hashes": bundle["file_hashes"],
                "result_command_digest": result["command_digest"],
            }
        )
    runs.sort(key=lambda value: cast(str, value["receipt_id"]))

    index: dict[str, object] = {
        "schema_version": PAIR_SCHEMA,
        "generated_at": now.isoformat(),
        "case_id": case.case_id,
        "sandbox_profile_id": profile.sandbox_profile_id,
        "benchmark_suite_digest": benchmark_suite_digest,
        "skill_artifact_digest": skill_artifact_digest,
        "issuer_key_id": issuer_key_id,
        "public_key_digest": sha256_bytes(trusted_public),
        "runs": runs,
        "controls": {
            "receipt_admitted_at_issued_time": True,
            "tamper_rejected_for_both": all(tamper_controls),
            "original_bundles_unchanged": True,
            "distinct_sandbox_names": True,
            "distinct_workspace_nonces": True,
            "distinct_receipt_ids": True,
            "same_image_digest": True,
            "same_policy_digest": True,
            "same_deterministic_result": True,
            "cleanup_verified_for_both": True,
            "workspace_destroyed_for_both": True,
            **history,
        },
    }
    index["pair_digest"] = sha256_bytes(_canonical_json_bytes(index))
    return index


def write_pair_index(path: Path | str, index: Mapping[str, object]) -> None:
    destination = Path(path)
    if destination.exists():
        raise EvidencePairError(f"output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
