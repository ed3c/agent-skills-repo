"""Versioned data model and deterministic digest helpers for sandbox execution."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Mapping, Protocol, Sequence, cast

from skill_arena.core import canonical_bytes
from skill_arena.sandbox_executor.errors import ExitCode, SandboxExecutorError

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
OPEN_SHELL_VERSION_RE = re.compile(r"(?:openshell\s+)?(?P<version>\d+\.\d+\.\d+)")
CASE_GROUPS = frozenset({"critical", "anchor", "target"})
CASE_SCHEMA = "sandbox-executor-case@1"
PROFILE_SCHEMA = "sandbox-profile@1"
TASK_RESULT_SCHEMA = "sandbox-task-result@1"
TASK_EVIDENCE_SCHEMA = "sandbox-task-evidence@1"
ATTESTATION_SCHEMA = "sandbox-attestation@1"
BUNDLE_SCHEMA = "sandbox-evidence-bundle@1"
RECEIPT_SCHEMA = "sandbox-case-receipt@1"


@dataclass(frozen=True)
class ResourceLimits:
    cpu_time_ms: int
    wall_time_ms: int
    memory_bytes: int
    process_count_max: int
    network_policy: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "ResourceLimits":
        required = {
            "cpu_time_ms",
            "wall_time_ms",
            "memory_bytes",
            "process_count_max",
            "network_policy",
        }
        if set(value) != required:
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID,
                "resource_limits fields mismatch: "
                f"expected={sorted(required)} actual={sorted(value)}",
            )
        integer_names = (
            "cpu_time_ms",
            "wall_time_ms",
            "memory_bytes",
            "process_count_max",
        )
        if any(
            type(value[name]) is not int or cast(int, value[name]) < 1
            for name in integer_names
        ):
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID, "resource limits must be positive integers"
            )
        network = value["network_policy"]
        if not isinstance(network, str) or not network:
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID, "network_policy must be a non-empty string"
            )
        return cls(
            cpu_time_ms=cast(int, value["cpu_time_ms"]),
            wall_time_ms=cast(int, value["wall_time_ms"]),
            memory_bytes=cast(int, value["memory_bytes"]),
            process_count_max=cast(int, value["process_count_max"]),
            network_policy=network,
        )


@dataclass(frozen=True)
class SandboxProfile:
    sandbox_profile_id: str
    openshell_version: str
    substrate: str
    target_host_profile_id: str
    target_host_version: str
    target_transport_profile: str
    target_policy_profile: str
    artifact_access_policy: str
    workspace_disposition: str
    secret_policy: str
    allowed_tools: tuple[str, ...]
    resource_limits: ResourceLimits
    evidence_ttl_seconds: int
    openshell_source_ref: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "SandboxProfile":
        required = {
            "schema_version",
            "sandbox_profile_id",
            "openshell_version",
            "openshell_source_ref",
            "substrate",
            "target_host_profile_id",
            "target_host_version",
            "target_transport_profile",
            "target_policy_profile",
            "artifact_access_policy",
            "workspace_disposition",
            "secret_policy",
            "allowed_tools",
            "resource_limits",
            "evidence_ttl_seconds",
        }
        if set(value) != required or value.get("schema_version") != PROFILE_SCHEMA:
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID, "sandbox profile schema or fields are invalid"
            )
        string_fields = required - {
            "schema_version",
            "allowed_tools",
            "resource_limits",
            "evidence_ttl_seconds",
        }
        if any(
            not isinstance(value[name], str) or not value[name]
            for name in string_fields
        ):
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID, "sandbox profile string field is invalid"
            )
        exact = {
            "openshell_version": "0.0.59",
            "openshell_source_ref": "NVIDIA/OpenShell@v0.0.59",
            "substrate": "docker",
            "target_transport_profile": "openshell-cli-create-command@0.0.59",
            "artifact_access_policy": "read-only@1",
            "workspace_disposition": "disposable@1",
            "secret_policy": "no-production-secrets@1",
        }
        for name, expected in exact.items():
            if value[name] != expected:
                raise SandboxExecutorError(
                    ExitCode.CONFIG_INVALID,
                    f"sandbox profile {name} must equal {expected!r}",
                )
        allowed = value["allowed_tools"]
        if (
            not isinstance(allowed, list)
            or not allowed
            or any(
                not isinstance(item, str) or not item.startswith("/")
                for item in allowed
            )
            or len(set(allowed)) != len(allowed)
            or allowed != sorted(allowed)
        ):
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID,
                "allowed_tools must be a sorted unique list of absolute executable paths",
            )
        ttl = value["evidence_ttl_seconds"]
        if type(ttl) is not int or not 1 <= cast(int, ttl) <= 86_400:
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID,
                "evidence_ttl_seconds must be within 1..86400",
            )
        limits = value["resource_limits"]
        if not isinstance(limits, Mapping):
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID, "resource_limits must be an object"
            )
        return cls(
            sandbox_profile_id=cast(str, value["sandbox_profile_id"]),
            openshell_version=cast(str, value["openshell_version"]),
            openshell_source_ref=cast(str, value["openshell_source_ref"]),
            substrate=cast(str, value["substrate"]),
            target_host_profile_id=cast(str, value["target_host_profile_id"]),
            target_host_version=cast(str, value["target_host_version"]),
            target_transport_profile=cast(str, value["target_transport_profile"]),
            target_policy_profile=cast(str, value["target_policy_profile"]),
            artifact_access_policy=cast(str, value["artifact_access_policy"]),
            workspace_disposition=cast(str, value["workspace_disposition"]),
            secret_policy=cast(str, value["secret_policy"]),
            allowed_tools=tuple(cast(list[str], allowed)),
            resource_limits=ResourceLimits.from_mapping(
                cast(Mapping[str, object], limits)
            ),
            evidence_ttl_seconds=cast(int, ttl),
        )

    @property
    def allowed_tools_digest(self) -> str:
        return sha256_json({"allowed_tools": list(self.allowed_tools)})


@dataclass(frozen=True)
class SandboxCase:
    case_id: str
    group: str
    command: tuple[str, ...]
    expected_exit_code: int
    expected_stdout_utf8: str
    expected_stderr_utf8: str
    expected_timed_out: bool
    expected_evidence_digest: str
    arena_case: Mapping[str, object]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "SandboxCase":
        required = {
            "schema_version",
            "case_id",
            "group",
            "command",
            "expected_exit_code",
            "expected_stdout_utf8",
            "expected_stderr_utf8",
            "expected_timed_out",
            "expected_evidence_digest",
            "arena_case",
        }
        if set(value) != required or value.get("schema_version") != CASE_SCHEMA:
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID, "sandbox case schema or fields are invalid"
            )
        case_id = value["case_id"]
        group = value["group"]
        command = value["command"]
        if not isinstance(case_id, str) or not case_id:
            raise SandboxExecutorError(ExitCode.CONFIG_INVALID, "case_id is invalid")
        if group not in CASE_GROUPS:
            raise SandboxExecutorError(ExitCode.CONFIG_INVALID, "case group is invalid")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(item, str) or not item for item in command)
            or not cast(str, command[0]).startswith("/")
        ):
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID,
                "command must start with an absolute executable",
            )
        if type(value["expected_exit_code"]) is not int:
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID, "expected_exit_code must be an integer"
            )
        for field in ("expected_stdout_utf8", "expected_stderr_utf8"):
            if not isinstance(value[field], str):
                raise SandboxExecutorError(
                    ExitCode.CONFIG_INVALID, f"{field} must be a string"
                )
        if type(value["expected_timed_out"]) is not bool:
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID, "expected_timed_out must be boolean"
            )
        expected_digest = value["expected_evidence_digest"]
        if (
            not isinstance(expected_digest, str)
            or not SHA256_RE.fullmatch(expected_digest)
        ):
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID, "expected_evidence_digest is invalid"
            )
        arena_case = value["arena_case"]
        expected_arena = {
            "case_id": case_id,
            "group": group,
            "passed": True,
            "evidence_digest": expected_digest,
        }
        if not isinstance(arena_case, Mapping) or dict(arena_case) != expected_arena:
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID,
                "arena_case must bind the deterministic successful outcome",
            )
        case = cls(
            case_id=case_id,
            group=cast(str, group),
            command=tuple(cast(list[str], command)),
            expected_exit_code=cast(int, value["expected_exit_code"]),
            expected_stdout_utf8=cast(str, value["expected_stdout_utf8"]),
            expected_stderr_utf8=cast(str, value["expected_stderr_utf8"]),
            expected_timed_out=cast(bool, value["expected_timed_out"]),
            expected_evidence_digest=expected_digest,
            arena_case=cast(Mapping[str, object], arena_case),
        )
        calculated = task_evidence_digest(
            command=case.command,
            exit_code=case.expected_exit_code,
            stdout=case.expected_stdout_utf8.encode("utf-8"),
            stderr=case.expected_stderr_utf8.encode("utf-8"),
            timed_out=case.expected_timed_out,
        )
        if calculated != expected_digest:
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID,
                f"expected_evidence_digest is stale: expected {calculated}",
            )
        return case


@dataclass(frozen=True)
class ExecutionRequest:
    sandbox_name: str
    workspace_nonce: str
    case: SandboxCase
    profile: SandboxProfile
    policy_path: Path
    runner_path: Path


@dataclass(frozen=True)
class DriverOutcome:
    result: Mapping[str, object]
    attestation: Mapping[str, object]
    sandbox_image_digest: str
    sandbox_policy_digest: str
    started_at: datetime
    completed_at: datetime
    cleanup_verified: bool


class SandboxDriver(Protocol):
    def execute(self, request: ExecutionRequest) -> DriverOutcome: ...


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_json(value: object) -> str:
    return sha256_bytes(canonical_bytes(value))


def command_digest(command: Sequence[str]) -> str:
    return sha256_json({"command": list(command)})


def task_evidence(
    *,
    command: Sequence[str],
    exit_code: int,
    stdout: bytes,
    stderr: bytes,
    timed_out: bool,
) -> dict[str, object]:
    return {
        "schema_version": TASK_EVIDENCE_SCHEMA,
        "command_digest": command_digest(command),
        "exit_code": exit_code,
        "stdout_sha256": sha256_bytes(stdout),
        "stderr_sha256": sha256_bytes(stderr),
        "timed_out": timed_out,
    }


def task_evidence_digest(
    *,
    command: Sequence[str],
    exit_code: int,
    stdout: bytes,
    stderr: bytes,
    timed_out: bool,
) -> str:
    return sha256_json(
        task_evidence(
            command=command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )
    )


def load_json_object(path: Path | str) -> dict[str, object]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SandboxExecutorError(
            ExitCode.CONFIG_INVALID, f"cannot read JSON object {source}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise SandboxExecutorError(
            ExitCode.CONFIG_INVALID, f"JSON root must be an object: {source}"
        )
    return cast(dict[str, object], value)
