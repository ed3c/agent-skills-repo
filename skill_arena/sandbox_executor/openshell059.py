"""OpenShell 0.0.59 CLI adapter with Docker-backed physical evidence."""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Protocol, Sequence, cast

from skill_arena.sandbox_executor.errors import ExitCode, SandboxExecutorError
from skill_arena.sandbox_executor.model import (
    ATTESTATION_SCHEMA,
    OPEN_SHELL_VERSION_RE,
    SHA256_RE,
    DriverOutcome,
    ExecutionRequest,
    SandboxProfile,
    sha256_bytes,
    sha256_json,
)


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes


class CommandRunner(Protocol):
    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
    ) -> CommandResult: ...


class SubprocessCommandRunner:
    """Small subprocess boundary kept injectable for contract tests."""

    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
        cwd: Path | None = None,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                list(args),
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SandboxExecutorError(
                ExitCode.SUBSTRATE_UNAVAILABLE,
                f"required executable is absent: {args[0]}",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxExecutorError(
                ExitCode.TRANSPORT_FAILED,
                f"control-plane command timed out: {args[0]}",
            ) from exc
        return CommandResult(
            tuple(args),
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )


class OpenShell059Driver:
    """Run one uploaded case through the pinned OpenShell CLI surface.

    Version 0.0.59 does not expose the newer stable non-interactive
    ``sandbox exec`` shape in its committed CLI reference. The task runner is
    sent as the trailing command of ``sandbox create``. The sandbox is kept
    alive for result/policy/container evidence and deleted in ``finally``.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        openshell_binary: str = "openshell",
        docker_binary: str = "docker",
    ) -> None:
        self.runner = runner or SubprocessCommandRunner()
        self.openshell_binary = openshell_binary
        self.docker_binary = docker_binary

    def build_create_command(
        self,
        request: ExecutionRequest,
        staged_case: Path,
    ) -> tuple[str, ...]:
        memory_mib = math.ceil(
            request.profile.resource_limits.memory_bytes / (1024 * 1024)
        )
        return (
            self.openshell_binary,
            "sandbox",
            "create",
            "--name",
            request.sandbox_name,
            "--policy",
            str(request.policy_path),
            "--cpu",
            "1",
            "--memory",
            f"{memory_mib}Mi",
            "--upload",
            f"{request.runner_path}:/sandbox/input/runner.py",
            "--upload",
            f"{staged_case}:/sandbox/input/case.json",
            "--no-auto-providers",
            "--no-tty",
            "--",
            "/usr/bin/python3",
            "/sandbox/input/runner.py",
            "/sandbox/input/case.json",
            "/sandbox/output/result.json",
        )

    def _checked(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float,
        code: ExitCode,
        label: str,
        cwd: Path | None = None,
    ) -> CommandResult:
        try:
            result = self.runner.run(
                args,
                timeout_seconds=timeout_seconds,
                cwd=cwd,
            )
        except SandboxExecutorError as exc:
            if exc.code == ExitCode.SUBSTRATE_UNAVAILABLE and code != exc.code:
                raise SandboxExecutorError(
                    code,
                    f"{label} failed: {exc}",
                ) from exc
            raise
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace")[-1000:]
            raise SandboxExecutorError(
                code,
                f"{label} failed: {stderr}",
            )
        return result

    def _preflight(self, profile: SandboxProfile) -> dict[str, object]:
        version_result = self._checked(
            (self.openshell_binary, "--version"),
            timeout_seconds=15,
            code=ExitCode.GATEWAY_UNAVAILABLE,
            label="openshell version probe",
        )
        version_text = (version_result.stdout + version_result.stderr).decode(
            "utf-8",
            errors="replace",
        )
        match = OPEN_SHELL_VERSION_RE.search(version_text)
        if match is None or match.group("version") != profile.openshell_version:
            raise SandboxExecutorError(
                ExitCode.GATEWAY_UNAVAILABLE,
                "OpenShell version mismatch: "
                f"expected {profile.openshell_version}, got {version_text.strip()!r}",
            )
        status = self._checked(
            (self.openshell_binary, "status"),
            timeout_seconds=20,
            code=ExitCode.GATEWAY_UNAVAILABLE,
            label="OpenShell gateway status",
        )
        docker = self._checked(
            (
                self.docker_binary,
                "version",
                "--format",
                "{{.Server.Version}}",
            ),
            timeout_seconds=20,
            code=ExitCode.SUBSTRATE_UNAVAILABLE,
            label="Docker server probe",
        )
        return {
            "openshell_version_stdout_sha256": sha256_bytes(
                version_result.stdout
            ),
            "gateway_status_stdout_sha256": sha256_bytes(status.stdout),
            "docker_server_version": docker.stdout.decode(
                "utf-8",
                errors="replace",
            ).strip(),
        }

    def _find_container(self, sandbox_name: str) -> Mapping[str, object]:
        listed = self._checked(
            (self.docker_binary, "ps", "-aq"),
            timeout_seconds=20,
            code=ExitCode.EVIDENCE_INCOMPLETE,
            label="Docker container listing",
        )
        ids = [
            line.strip()
            for line in listed.stdout.decode().splitlines()
            if line.strip()
        ]
        if not ids:
            raise SandboxExecutorError(
                ExitCode.EVIDENCE_INCOMPLETE,
                "OpenShell Docker container was not found",
            )
        inspected = self._checked(
            (self.docker_binary, "inspect", *ids),
            timeout_seconds=30,
            code=ExitCode.EVIDENCE_INCOMPLETE,
            label="Docker container inspect",
        )
        try:
            objects = json.loads(inspected.stdout)
        except json.JSONDecodeError as exc:
            raise SandboxExecutorError(
                ExitCode.EVIDENCE_INCOMPLETE,
                "Docker inspect output is invalid JSON",
            ) from exc
        matches: list[Mapping[str, object]] = []
        for item in objects if isinstance(objects, list) else []:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("Name", "")).lstrip("/")
            config = item.get("Config")
            labels = config.get("Labels") if isinstance(config, Mapping) else None
            label_match = False
            if isinstance(labels, Mapping):
                label_match = any(
                    str(key).startswith("openshell.ai/")
                    and sandbox_name in str(value)
                    for key, value in labels.items()
                )
            if label_match or sandbox_name in name:
                matches.append(cast(Mapping[str, object], item))
        if len(matches) != 1:
            raise SandboxExecutorError(
                ExitCode.EVIDENCE_INCOMPLETE,
                f"expected one OpenShell container for {sandbox_name}, "
                f"found {len(matches)}",
            )
        return matches[0]

    @staticmethod
    def _locate_result(download_root: Path) -> Path:
        candidates = sorted(download_root.rglob("result.json"))
        if len(candidates) != 1 or candidates[0].is_symlink():
            raise SandboxExecutorError(
                ExitCode.TRANSPORT_FAILED,
                f"expected one downloaded result.json, found {len(candidates)}",
            )
        return candidates[0]

    def _delete_and_verify(
        self,
        request: ExecutionRequest,
        container_id: str | None,
    ) -> None:
        delete = self.runner.run(
            (
                self.openshell_binary,
                "sandbox",
                "delete",
                request.sandbox_name,
            ),
            timeout_seconds=45,
        )
        if delete.returncode != 0:
            raise SandboxExecutorError(
                ExitCode.SANDBOX_DELETE_FAILED,
                "OpenShell sandbox deletion failed; no receipt may be signed",
            )
        names = self.runner.run(
            (
                self.openshell_binary,
                "sandbox",
                "list",
                "--names",
            ),
            timeout_seconds=20,
        )
        still_listed = request.sandbox_name in names.stdout.decode(
            "utf-8",
            errors="replace",
        ).splitlines()
        container_alive = False
        if container_id:
            post = self.runner.run(
                (self.docker_binary, "inspect", container_id),
                timeout_seconds=20,
            )
            container_alive = post.returncode == 0
        if names.returncode != 0 or still_listed or container_alive:
            raise SandboxExecutorError(
                ExitCode.SANDBOX_DELETE_FAILED,
                "sandbox or backing container remains after deletion",
            )

    def execute(self, request: ExecutionRequest) -> DriverOutcome:
        control = self._preflight(request.profile)
        try:
            control.update(
                {
                    "runner_bytes_sha256": sha256_bytes(
                        request.runner_path.read_bytes()
                    ),
                    "requested_policy_bytes_sha256": sha256_bytes(
                        request.policy_path.read_bytes()
                    ),
                    "resource_enforcement": (
                        "openshell-limits+runner-rlimit@1"
                    ),
                    "secrets_scrubbed": True,
                    "auto_providers_disabled": True,
                }
            )
        except OSError as exc:
            raise SandboxExecutorError(
                ExitCode.CONFIG_INVALID,
                f"cannot read runner or policy input: {exc}",
            ) from exc

        created = False
        create_attempted = False
        container_id: str | None = None
        primary_error: BaseException | None = None
        started = datetime.now(timezone.utc)
        outcome_values: dict[str, object] = {}

        with tempfile.TemporaryDirectory(prefix="arena-openshell-") as temporary:
            temp = Path(temporary)
            staged_case = temp / "case.json"
            runner_input = {
                "schema_version": "sandbox-runner-input@1",
                "workspace_nonce": request.workspace_nonce,
                "command": list(request.case.command),
                "allowed_tools": list(request.profile.allowed_tools),
                "resource_limits": asdict(request.profile.resource_limits),
            }
            staged_case.write_text(
                json.dumps(
                    runner_input,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            control["runner_input_bytes_sha256"] = sha256_bytes(
                staged_case.read_bytes()
            )
            download_root = temp / "download"
            download_root.mkdir()
            try:
                create_attempted = True
                create = self._checked(
                    self.build_create_command(request, staged_case),
                    timeout_seconds=max(
                        60.0,
                        request.profile.resource_limits.wall_time_ms / 1000 + 60,
                    ),
                    code=ExitCode.SANDBOX_CREATE_FAILED,
                    label="OpenShell sandbox create/command",
                )
                created = True
                policy = self._checked(
                    (
                        self.openshell_binary,
                        "sandbox",
                        "get",
                        request.sandbox_name,
                        "--policy-only",
                    ),
                    timeout_seconds=20,
                    code=ExitCode.EVIDENCE_INCOMPLETE,
                    label="OpenShell effective policy retrieval",
                )
                self._checked(
                    (
                        self.openshell_binary,
                        "sandbox",
                        "download",
                        request.sandbox_name,
                        "/sandbox/output",
                        str(download_root),
                    ),
                    timeout_seconds=45,
                    code=ExitCode.TRANSPORT_FAILED,
                    label="OpenShell result download",
                )
                result_path = self._locate_result(download_root)
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SandboxExecutorError(
                        ExitCode.TRANSPORT_FAILED,
                        f"downloaded task result is unreadable: {exc}",
                    ) from exc
                if not isinstance(result, dict):
                    raise SandboxExecutorError(
                        ExitCode.TRANSPORT_FAILED,
                        "downloaded task result is not an object",
                    )
                container = self._find_container(request.sandbox_name)
                container_id = str(container.get("Id", ""))
                image_digest = str(container.get("Image", ""))
                if not SHA256_RE.fullmatch(image_digest):
                    raise SandboxExecutorError(
                        ExitCode.EVIDENCE_INCOMPLETE,
                        "Docker image identity is not immutable sha256: "
                        f"{image_digest!r}",
                    )
                host_config = container.get("HostConfig")
                memory_observed = (
                    host_config.get("Memory")
                    if isinstance(host_config, Mapping)
                    else None
                )
                if (
                    type(memory_observed) is not int
                    or cast(int, memory_observed)
                    < request.profile.resource_limits.memory_bytes
                ):
                    raise SandboxExecutorError(
                        ExitCode.EVIDENCE_INCOMPLETE,
                        "Docker memory limit is absent or below the declared profile",
                    )
                completed = datetime.now(timezone.utc)
                outcome_values = {
                    "result": cast(Mapping[str, object], result),
                    "image_digest": image_digest,
                    "policy_digest": sha256_bytes(policy.stdout),
                    "completed": completed,
                    "control": {
                        **control,
                        "create_stdout_sha256": sha256_bytes(create.stdout),
                        "effective_policy_bytes_sha256": sha256_bytes(
                            policy.stdout
                        ),
                        "docker_container_inspect_sha256": sha256_json(container),
                        "docker_container_id": container_id,
                        "docker_memory_bytes": cast(int, memory_observed),
                    },
                }
            except BaseException as exc:
                primary_error = exc
            finally:
                if create_attempted and not created:
                    names_probe = self.runner.run(
                        (
                            self.openshell_binary,
                            "sandbox",
                            "list",
                            "--names",
                        ),
                        timeout_seconds=20,
                    )
                    if names_probe.returncode != 0:
                        primary_error = SandboxExecutorError(
                            ExitCode.SANDBOX_DELETE_FAILED,
                            "cannot prove a failed create left no sandbox residue",
                        )
                    else:
                        created = request.sandbox_name in names_probe.stdout.decode(
                            "utf-8",
                            errors="replace",
                        ).splitlines()
                if created:
                    try:
                        self._delete_and_verify(request, container_id)
                    except SandboxExecutorError as exc:
                        primary_error = exc
                if primary_error is not None:
                    raise primary_error

            result = cast(Mapping[str, object], outcome_values["result"])
            image_digest = cast(str, outcome_values["image_digest"])
            policy_digest = cast(str, outcome_values["policy_digest"])
            completed = cast(datetime, outcome_values["completed"])
            attestation = {
                "schema_version": ATTESTATION_SCHEMA,
                "sandbox_name": request.sandbox_name,
                "workspace_nonce": request.workspace_nonce,
                "openshell_version": request.profile.openshell_version,
                "openshell_source_ref": request.profile.openshell_source_ref,
                "substrate": request.profile.substrate,
                "transport": "openshell-cli-create-command@0.0.59",
                "sandbox_image_digest": image_digest,
                "sandbox_policy_digest": policy_digest,
                "resource_limits": asdict(request.profile.resource_limits),
                "cleanup_verified": True,
                "workspace_destroyed": True,
                "started_at": started.astimezone(timezone.utc).isoformat(),
                "completed_at": completed.astimezone(timezone.utc).isoformat(),
                "control_evidence": cast(
                    Mapping[str, object],
                    outcome_values["control"],
                ),
            }
            return DriverOutcome(
                result=result,
                attestation=attestation,
                sandbox_image_digest=image_digest,
                sandbox_policy_digest=policy_digest,
                started_at=started,
                completed_at=completed,
                cleanup_verified=True,
            )
