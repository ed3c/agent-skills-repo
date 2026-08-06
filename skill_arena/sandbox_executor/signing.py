"""Signing, validation, and atomic publication for sandbox evidence bundles."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping, cast

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from skill_arena.core import SANDBOX_CASE_SIGNATURE_DOMAIN, canonical_bytes
from skill_arena.sandbox_executor.errors import ExitCode, SandboxExecutorError
from skill_arena.sandbox_executor.model import (
    ATTESTATION_SCHEMA,
    BUNDLE_SCHEMA,
    RECEIPT_SCHEMA,
    SHA256_RE,
    DriverOutcome,
    ExecutionRequest,
    SandboxCase,
    SandboxDriver,
    SandboxProfile,
    command_digest,
    sha256_bytes,
    sha256_json,
    task_evidence_digest,
)


def load_private_key(
    path: Path | str, *, repo_root: Path | str, issuer_key_id: str
) -> Ed25519PrivateKey:
    if not issuer_key_id.startswith("dev-") or len(issuer_key_id) < 5:
        raise SandboxExecutorError(
            ExitCode.SIGNING_KEY_INVALID,
            "sandbox receipt key id must be visibly development-scoped (dev-*)",
        )
    source = Path(path).expanduser().resolve()
    repository = Path(repo_root).resolve()
    if source == repository or repository in source.parents:
        raise SandboxExecutorError(
            ExitCode.SIGNING_KEY_INVALID,
            "sandbox receipt private key must live outside the repository",
        )
    try:
        mode = stat.S_IMODE(source.stat().st_mode)
        data = source.read_bytes()
    except OSError as exc:
        raise SandboxExecutorError(
            ExitCode.SIGNING_KEY_INVALID, f"cannot read signing key: {exc}"
        ) from exc
    if mode & 0o077:
        raise SandboxExecutorError(
            ExitCode.SIGNING_KEY_INVALID,
            f"signing key permissions must be owner-only, found {oct(mode)}",
        )
    try:
        if data.startswith(b"-----BEGIN"):
            loaded = serialization.load_pem_private_key(data, password=None)
            if not isinstance(loaded, Ed25519PrivateKey):
                raise TypeError("not Ed25519")
            return loaded
        if len(data) == 32:
            return Ed25519PrivateKey.from_private_bytes(data)
    except (TypeError, ValueError) as exc:
        raise SandboxExecutorError(
            ExitCode.SIGNING_KEY_INVALID,
            "signing key is not a valid Ed25519 private key",
        ) from exc
    raise SandboxExecutorError(
        ExitCode.SIGNING_KEY_INVALID,
        "signing key must be 32 raw Ed25519 bytes or an unencrypted PEM",
    )


def _parse_task_result(
    value: Mapping[str, object], request: ExecutionRequest
) -> tuple[bytes, bytes]:
    required = {
        "schema_version",
        "workspace_nonce",
        "command_digest",
        "exit_code",
        "stdout_base64",
        "stderr_base64",
        "stdout_sha256",
        "stderr_sha256",
        "timed_out",
        "started_at",
        "completed_at",
    }
    if set(value) != required or value.get("schema_version") != "sandbox-task-result@1":
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "sandbox task result schema or fields are invalid",
        )
    if value["workspace_nonce"] != request.workspace_nonce:
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "sandbox task result belongs to a different invocation",
        )
    if value["command_digest"] != command_digest(request.case.command):
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "sandbox task command digest mismatch",
        )
    if type(value["exit_code"]) is not int or type(value["timed_out"]) is not bool:
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "sandbox task result types are invalid",
        )
    try:
        stdout = base64.b64decode(cast(str, value["stdout_base64"]), validate=True)
        stderr = base64.b64decode(cast(str, value["stderr_base64"]), validate=True)
    except (ValueError, TypeError) as exc:
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "sandbox task result output is not valid base64",
        ) from exc
    if (
        value["stdout_sha256"] != sha256_bytes(stdout)
        or value["stderr_sha256"] != sha256_bytes(stderr)
    ):
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "sandbox task output digest mismatch",
        )
    for field in ("started_at", "completed_at"):
        try:
            parsed = datetime.fromisoformat(cast(str, value[field]))
        except (TypeError, ValueError) as exc:
            raise SandboxExecutorError(
                ExitCode.EVIDENCE_INCOMPLETE,
                f"sandbox result {field} is invalid",
            ) from exc
        if parsed.tzinfo is None:
            raise SandboxExecutorError(
                ExitCode.EVIDENCE_INCOMPLETE,
                f"sandbox result {field} must include timezone",
            )
    return stdout, stderr


def _validate_attestation(outcome: DriverOutcome, request: ExecutionRequest) -> None:
    if not outcome.cleanup_verified:
        raise SandboxExecutorError(
            ExitCode.SANDBOX_DELETE_FAILED,
            "sandbox cleanup was not verified",
        )
    attestation = outcome.attestation
    required = {
        "schema_version",
        "sandbox_name",
        "workspace_nonce",
        "openshell_version",
        "openshell_source_ref",
        "substrate",
        "transport",
        "sandbox_image_digest",
        "sandbox_policy_digest",
        "resource_limits",
        "cleanup_verified",
        "workspace_destroyed",
        "started_at",
        "completed_at",
        "control_evidence",
    }
    if (
        set(attestation) != required
        or attestation.get("schema_version") != ATTESTATION_SCHEMA
    ):
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "sandbox attestation schema or fields are invalid",
        )
    expected = {
        "sandbox_name": request.sandbox_name,
        "workspace_nonce": request.workspace_nonce,
        "openshell_version": request.profile.openshell_version,
        "openshell_source_ref": request.profile.openshell_source_ref,
        "substrate": request.profile.substrate,
        "sandbox_image_digest": outcome.sandbox_image_digest,
        "sandbox_policy_digest": outcome.sandbox_policy_digest,
        "resource_limits": asdict(request.profile.resource_limits),
        "cleanup_verified": True,
        "workspace_destroyed": True,
    }
    if any(attestation.get(field) != value for field, value in expected.items()):
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "sandbox attestation binding mismatch",
        )
    if attestation.get("transport") != "openshell-cli-create-command@0.0.59":
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "sandbox attestation transport is invalid",
        )
    if not SHA256_RE.fullmatch(
        outcome.sandbox_image_digest
    ) or not SHA256_RE.fullmatch(outcome.sandbox_policy_digest):
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "sandbox image or policy digest is invalid",
        )
    control = attestation.get("control_evidence")
    if not isinstance(control, Mapping) or not control:
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "sandbox control evidence is absent",
        )


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise SandboxExecutorError(
            ExitCode.EVIDENCE_INCOMPLETE,
            "executor timestamps must include timezone",
        )
    return value.astimezone(timezone.utc).isoformat()


def _write_bundle_atomic(
    output_dir: Path,
    *,
    receipt: Mapping[str, object],
    attestation: Mapping[str, object],
    result: Mapping[str, object],
) -> None:
    if output_dir.exists():
        raise SandboxExecutorError(
            ExitCode.OUTPUT_EXISTS,
            f"output bundle already exists: {output_dir}",
        )
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent)
    )
    try:
        payloads = {
            "receipt.json": receipt,
            "attestation.json": attestation,
            "result.json": result,
        }
        files: list[dict[str, str]] = []
        for name, value in payloads.items():
            data = json.dumps(
                value, ensure_ascii=False, indent=2, sort_keys=True
            ).encode("utf-8") + b"\n"
            (temporary / name).write_bytes(data)
            files.append(
                {"path": name, "sha256": sha256_bytes(data).removeprefix("sha256:")}
            )
        ordered = sorted(files, key=lambda item: item["path"])
        manifest = {
            "schema_version": BUNDLE_SCHEMA,
            "files": ordered,
            "bundle_digest": sha256_json({"files": ordered}),
        }
        (temporary / "bundle-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output_dir)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def execute_case_to_bundle(
    *,
    case: SandboxCase,
    profile: SandboxProfile,
    policy_path: Path | str,
    runner_path: Path | str,
    driver: SandboxDriver,
    private_key: Ed25519PrivateKey,
    issuer_key_id: str,
    output_dir: Path | str,
    benchmark_suite_digest: str,
    skill_artifact_digest: str,
    now: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    for label, digest in (
        ("benchmark_suite_digest", benchmark_suite_digest),
        ("skill_artifact_digest", skill_artifact_digest),
    ):
        if not SHA256_RE.fullmatch(digest):
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID,
                f"{label} is invalid",
            )
    for label, source in (
        ("policy", Path(policy_path)),
        ("runner", Path(runner_path)),
    ):
        if source.is_symlink() or not source.is_file():
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID,
                f"{label} input must be a regular non-symlink file: {source}",
            )
    if Path(output_dir).exists():
        raise SandboxExecutorError(
            ExitCode.OUTPUT_EXISTS,
            f"output bundle already exists: {output_dir}",
        )
    if case.command[0] not in profile.allowed_tools:
        raise SandboxExecutorError(
            ExitCode.CONFIG_INVALID,
            f"case executable is not in allowed_tools: {case.command[0]}",
        )
    if not issuer_key_id.startswith("dev-"):
        raise SandboxExecutorError(
            ExitCode.SIGNING_KEY_INVALID,
            "issuer_key_id must start with dev-",
        )
    invocation = run_id or uuid.uuid4().hex
    if not re.fullmatch(r"[a-z0-9-]{8,64}", invocation):
        raise SandboxExecutorError(
            ExitCode.CONFIG_INVALID,
            "run_id is invalid",
        )
    sandbox_name = f"arena-{case.case_id}-{invocation[:12]}"
    if len(sandbox_name) > 63:
        sandbox_name = f"arena-{sha256_bytes(sandbox_name.encode())[7:27]}"
    request = ExecutionRequest(
        sandbox_name=sandbox_name,
        workspace_nonce=uuid.uuid4().hex,
        case=case,
        profile=profile,
        policy_path=Path(policy_path),
        runner_path=Path(runner_path),
    )
    outcome = driver.execute(request)
    stdout, stderr = _parse_task_result(outcome.result, request)
    _validate_attestation(outcome, request)
    elapsed_ms = (outcome.completed_at - outcome.started_at).total_seconds() * 1000
    if elapsed_ms < 0 or elapsed_ms > profile.resource_limits.wall_time_ms:
        raise SandboxExecutorError(
            ExitCode.WALL_WINDOW_EXCEEDED,
            f"physical run window exceeds wall limit: {elapsed_ms:.3f}ms",
        )
    actual_digest = task_evidence_digest(
        command=case.command,
        exit_code=cast(int, outcome.result["exit_code"]),
        stdout=stdout,
        stderr=stderr,
        timed_out=cast(bool, outcome.result["timed_out"]),
    )
    if actual_digest != case.expected_evidence_digest:
        raise SandboxExecutorError(
            ExitCode.TASK_RESULT_MISMATCH,
            "task result does not match preregistered evidence: "
            f"{actual_digest}",
        )
    issued_at = now or datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=profile.evidence_ttl_seconds)
    payload: dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA,
        "receipt_id": f"sandbox-{case.case_id}-{invocation}",
        "case_id": case.case_id,
        "case_group": case.group,
        "case_input_digest": sha256_json(case.arena_case),
        "benchmark_suite_digest": benchmark_suite_digest,
        "skill_artifact_digest": skill_artifact_digest,
        "target_host_profile_id": profile.target_host_profile_id,
        "target_host_version": profile.target_host_version,
        "target_transport_profile": profile.target_transport_profile,
        "target_policy_profile": profile.target_policy_profile,
        "sandbox_profile_id": profile.sandbox_profile_id,
        "sandbox_image_digest": outcome.sandbox_image_digest,
        "sandbox_policy_digest": outcome.sandbox_policy_digest,
        "sandbox_attestation_evidence_digest": sha256_json(outcome.attestation),
        "artifact_access_policy": profile.artifact_access_policy,
        "workspace_disposition": profile.workspace_disposition,
        "secret_policy": profile.secret_policy,
        "allowed_tools_digest": profile.allowed_tools_digest,
        "resource_limits": asdict(profile.resource_limits),
        "passed": True,
        "output_evidence_digest": actual_digest,
        "started_at": _iso(outcome.started_at),
        "completed_at": _iso(outcome.completed_at),
        "issued_at": _iso(issued_at),
        "expires_at": _iso(expires_at),
    }
    raw = canonical_bytes(payload)
    receipt: dict[str, object] = {
        "payload": payload,
        "receipt_hash": sha256_bytes(raw),
        "issuer_key_id": issuer_key_id,
        "signature": base64.b64encode(
            private_key.sign(SANDBOX_CASE_SIGNATURE_DOMAIN + raw)
        ).decode("ascii"),
    }
    _write_bundle_atomic(
        Path(output_dir),
        receipt=receipt,
        attestation=outcome.attestation,
        result=outcome.result,
    )
    return receipt
