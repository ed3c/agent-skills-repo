from __future__ import annotations

import base64
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator

from skill_arena.core import EvidenceRejected, verify_sandbox_case_receipts
from skill_arena.sandbox_executor import (
    ATTESTATION_SCHEMA,
    TASK_RESULT_SCHEMA,
    CommandResult,
    DriverOutcome,
    ExecutionRequest,
    ExitCode,
    OpenShell059Driver,
    SandboxCase,
    SandboxExecutorError,
    SandboxProfile,
    command_digest,
    execute_case_to_bundle,
    load_json_object,
    load_private_key,
    sha256_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = ROOT / "data/sandbox_cases/smoke-python.json"
PROFILE_PATH = ROOT / "data/sandbox_profiles/openshell-0.0.59-docker.json"
POLICY_PATH = ROOT / "data/sandbox_profiles/no-network.policy.yaml"
RUNNER_PATH = ROOT / "scripts/sandbox_case_runner.py"
BENCHMARK_DIGEST = "sha256:" + "1" * 64
SKILL_DIGEST = "sha256:" + "2" * 64
IMAGE_DIGEST = "sha256:" + "3" * 64
POLICY_DIGEST = "sha256:" + "4" * 64
NOW = datetime(2026, 8, 6, 12, 0, 5, tzinfo=timezone.utc)


def case() -> SandboxCase:
    return SandboxCase.from_mapping(load_json_object(CASE_PATH))


def profile() -> SandboxProfile:
    return SandboxProfile.from_mapping(load_json_object(PROFILE_PATH))


class FakeDriver:
    def __init__(self, *, cleanup=True, stdout=b"arena-smoke-v1\n", error=None):
        self.cleanup = cleanup
        self.stdout = stdout
        self.error = error
        self.requests = []

    def execute(self, request: ExecutionRequest) -> DriverOutcome:
        self.requests.append(request)
        if self.error:
            raise self.error
        started = NOW - timedelta(seconds=2)
        completed = NOW - timedelta(seconds=1)
        result = {
            "schema_version": TASK_RESULT_SCHEMA,
            "workspace_nonce": request.workspace_nonce,
            "command_digest": command_digest(request.case.command),
            "exit_code": 0,
            "stdout_base64": base64.b64encode(self.stdout).decode(),
            "stderr_base64": "",
            "stdout_sha256": sha256_bytes(self.stdout),
            "stderr_sha256": sha256_bytes(b""),
            "timed_out": False,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
        }
        attestation = {
            "schema_version": ATTESTATION_SCHEMA,
            "sandbox_name": request.sandbox_name,
            "workspace_nonce": request.workspace_nonce,
            "openshell_version": request.profile.openshell_version,
            "openshell_source_ref": request.profile.openshell_source_ref,
            "substrate": request.profile.substrate,
            "transport": "openshell-cli-create-command@0.0.59",
            "sandbox_image_digest": IMAGE_DIGEST,
            "sandbox_policy_digest": POLICY_DIGEST,
            "resource_limits": asdict(request.profile.resource_limits),
            "cleanup_verified": self.cleanup,
            "workspace_destroyed": self.cleanup,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "control_evidence": {"driver": "fake"},
        }
        return DriverOutcome(
            result=result,
            attestation=attestation,
            sandbox_image_digest=IMAGE_DIGEST,
            sandbox_policy_digest=POLICY_DIGEST,
            started_at=started,
            completed_at=completed,
            cleanup_verified=self.cleanup,
        )


def run_bundle(tmp_path: Path, driver, key, name="bundle", run_id="run-00000001"):
    return execute_case_to_bundle(
        case=case(),
        profile=profile(),
        policy_path=POLICY_PATH,
        runner_path=RUNNER_PATH,
        driver=driver,
        private_key=key,
        issuer_key_id="dev-test-key",
        output_dir=tmp_path / name,
        benchmark_suite_digest=BENCHMARK_DIGEST,
        skill_artifact_digest=SKILL_DIGEST,
        now=NOW,
        run_id=run_id,
    )


def admit(receipt, key):
    p = profile()
    verify_sandbox_case_receipts(
        [receipt],
        {"dev-test-key": key.public_key().public_bytes_raw()},
        expected_cases=[case().arena_case],
        expected_benchmark_suite_digest=BENCHMARK_DIGEST,
        expected_skill_artifact_digest=SKILL_DIGEST,
        expected_target_host_profile_id=p.target_host_profile_id,
        expected_target_host_version=p.target_host_version,
        expected_target_transport_profile=p.target_transport_profile,
        expected_target_policy_profile=p.target_policy_profile,
        expected_sandbox_profile_id=p.sandbox_profile_id,
        expected_sandbox_image_digest=IMAGE_DIGEST,
        expected_sandbox_policy_digest=POLICY_DIGEST,
        expected_artifact_access_policy=p.artifact_access_policy,
        expected_workspace_disposition=p.workspace_disposition,
        expected_secret_policy=p.secret_policy,
        expected_allowed_tools_digest=p.allowed_tools_digest,
        expected_cpu_time_ms=p.resource_limits.cpu_time_ms,
        expected_wall_time_ms=p.resource_limits.wall_time_ms,
        expected_memory_bytes=p.resource_limits.memory_bytes,
        expected_process_count_max=p.resource_limits.process_count_max,
        expected_network_policy=p.resource_limits.network_policy,
        now=NOW + timedelta(seconds=1),
    )


def test_receipt_is_admitted_and_tamper_is_rejected(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    receipt = run_bundle(tmp_path, FakeDriver(), key)
    admit(receipt, key)
    assert sorted(path.name for path in (tmp_path / "bundle").iterdir()) == [
        "attestation.json",
        "bundle-manifest.json",
        "receipt.json",
        "result.json",
    ]
    receipt["payload"]["sandbox_policy_digest"] = "sha256:" + "9" * 64
    with pytest.raises(EvidenceRejected):
        admit(receipt, key)


def test_fresh_invocations_never_share_identity(tmp_path: Path) -> None:
    driver = FakeDriver()
    for index in (1, 2):
        run_bundle(
            tmp_path,
            driver,
            Ed25519PrivateKey.generate(),
            name=f"bundle-{index}",
            run_id=f"run-0000000{index}",
        )
    assert len({item.sandbox_name for item in driver.requests}) == 2
    assert len({item.workspace_nonce for item in driver.requests}) == 2


@pytest.mark.parametrize(
    ("driver", "expected_code"),
    [
        (FakeDriver(cleanup=False), ExitCode.SANDBOX_DELETE_FAILED),
        (
            FakeDriver(
                error=SandboxExecutorError(
                    ExitCode.TRANSPORT_FAILED,
                    "transport dropped",
                )
            ),
            ExitCode.TRANSPORT_FAILED,
        ),
        (FakeDriver(stdout=b"wrong\n"), ExitCode.TASK_RESULT_MISMATCH),
    ],
)
def test_failures_write_no_signed_bundle(tmp_path, driver, expected_code) -> None:
    with pytest.raises(SandboxExecutorError) as raised:
        run_bundle(tmp_path, driver, Ed25519PrivateKey.generate())
    assert raised.value.code == expected_code
    assert not (tmp_path / "bundle").exists()


def test_development_key_is_external_owner_only_and_dev_scoped(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    inside = repo / "key"
    inside.write_bytes(Ed25519PrivateKey.generate().private_bytes_raw())
    inside.chmod(0o600)
    with pytest.raises(SandboxExecutorError, match="outside the repository"):
        load_private_key(inside, repo_root=repo, issuer_key_id="dev-key")

    outside = tmp_path / "outside.key"
    outside.write_bytes(Ed25519PrivateKey.generate().private_bytes_raw())
    outside.chmod(0o644)
    with pytest.raises(SandboxExecutorError, match="owner-only"):
        load_private_key(outside, repo_root=repo, issuer_key_id="dev-key")
    outside.chmod(0o600)
    assert isinstance(
        load_private_key(outside, repo_root=repo, issuer_key_id="dev-key"),
        Ed25519PrivateKey,
    )
    with pytest.raises(SandboxExecutorError, match="development-scoped"):
        load_private_key(outside, repo_root=repo, issuer_key_id="prod-key")


def test_committed_profile_case_and_generated_bundle_validate(tmp_path: Path) -> None:
    schema = json.loads((ROOT / "contracts/sandbox-executor.schema.json").read_text())
    validator = Draft202012Validator(schema)
    for value in (load_json_object(PROFILE_PATH), load_json_object(CASE_PATH)):
        assert list(validator.iter_errors(value)) == []
    run_bundle(tmp_path, FakeDriver(), Ed25519PrivateKey.generate())
    for name in ("attestation.json", "bundle-manifest.json"):
        value = json.loads((tmp_path / "bundle" / name).read_text())
        assert list(validator.iter_errors(value)) == []


def test_in_sandbox_runner_scrubs_environment_and_hashes_output(tmp_path: Path) -> None:
    sandbox = tmp_path / "sandbox"
    source = tmp_path / "input.json"
    output = sandbox / "output/result.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": "sandbox-runner-input@1",
                "workspace_nonce": "runner-nonce-001",
                "command": list(case().command),
                "allowed_tools": list(profile().allowed_tools),
                "resource_limits": asdict(profile().resource_limits),
            }
        )
    )
    env = dict(os.environ)
    env.update(
        {
            "ARENA_SANDBOX_ROOT": str(sandbox),
            "OPENAI_API_KEY": "must-not-reach-child",
        }
    )
    completed = subprocess.run(
        ["python", str(RUNNER_PATH), str(source), str(output)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(output.read_text())
    assert result["stdout_sha256"] == sha256_bytes(b"arena-smoke-v1\n")
    assert result["timed_out"] is False


class ScriptedRunner:
    def __init__(self, *, delete_fails=False, download_fails=False):
        self.delete_fails = delete_fails
        self.download_fails = download_fails
        self.deleted = False
        self.commands = []
        self.runner_input = None
        self.sandbox_name = ""

    @staticmethod
    def result(args, code=0, out=b"", err=b""):
        return CommandResult(tuple(args), code, out, err)

    def run(self, args, *, timeout_seconds, cwd=None):
        command = tuple(args)
        self.commands.append(command)
        if command == ("openshell", "--version"):
            return self.result(command, out=b"openshell 0.0.59\n")
        if command == ("openshell", "status"):
            return self.result(command, out=b"ready\n")
        if command[:3] == ("docker", "version", "--format"):
            return self.result(command, out=b"27.0.0\n")
        if command[:3] == ("openshell", "sandbox", "create"):
            self.sandbox_name = command[command.index("--name") + 1]
            uploads = [
                command[i + 1]
                for i, value in enumerate(command)
                if value == "--upload"
            ]
            source = next(
                Path(item.split(":", 1)[0])
                for item in uploads
                if item.endswith(":/sandbox/input/case.json")
            )
            self.runner_input = json.loads(source.read_text())
            return self.result(command, out=b"created\n")
        if command[:3] == ("openshell", "sandbox", "get"):
            return self.result(command, out=b"version: 1\nnetwork_policies: {}\n")
        if command[:3] == ("openshell", "sandbox", "download"):
            if self.download_fails:
                return self.result(command, code=1, err=b"dropped")
            destination = Path(command[-1])
            destination.mkdir(parents=True, exist_ok=True)
            now = datetime.now(timezone.utc)
            stdout = b"arena-smoke-v1\n"
            result = {
                "schema_version": TASK_RESULT_SCHEMA,
                "workspace_nonce": self.runner_input["workspace_nonce"],
                "command_digest": command_digest(self.runner_input["command"]),
                "exit_code": 0,
                "stdout_base64": base64.b64encode(stdout).decode(),
                "stderr_base64": "",
                "stdout_sha256": sha256_bytes(stdout),
                "stderr_sha256": sha256_bytes(b""),
                "timed_out": False,
                "started_at": (now - timedelta(milliseconds=20)).isoformat(),
                "completed_at": now.isoformat(),
            }
            (destination / "result.json").write_text(json.dumps(result))
            return self.result(command)
        if command == ("docker", "ps", "-aq"):
            return self.result(command, out=b"container-001\n")
        if command == ("docker", "inspect", "container-001"):
            if self.deleted:
                return self.result(command, code=1, err=b"not found")
            container = [
                {
                    "Id": "container-001",
                    "Name": f"/{self.sandbox_name}",
                    "Image": IMAGE_DIGEST,
                    "Config": {
                        "Labels": {"openshell.ai/sandbox-name": self.sandbox_name}
                    },
                    "HostConfig": {"Memory": 268435456},
                }
            ]
            return self.result(command, out=json.dumps(container).encode())
        if command[:3] == ("openshell", "sandbox", "delete"):
            if self.delete_fails:
                return self.result(command, code=1, err=b"delete failed")
            self.deleted = True
            return self.result(command)
        if command == ("openshell", "sandbox", "list", "--names"):
            out = b"" if self.deleted else (self.sandbox_name + "\n").encode()
            return self.result(command, out=out)
        raise AssertionError(command)


def test_openshell_control_contract_collects_evidence_and_cleans_up() -> None:
    runner = ScriptedRunner()
    driver = OpenShell059Driver(runner=runner)
    request = ExecutionRequest(
        sandbox_name="arena-smoke-python-abcdef123456",
        workspace_nonce="b" * 32,
        case=case(),
        profile=profile(),
        policy_path=POLICY_PATH,
        runner_path=RUNNER_PATH,
    )
    outcome = driver.execute(request)
    assert outcome.cleanup_verified is True
    assert outcome.sandbox_image_digest == IMAGE_DIGEST
    assert outcome.attestation["control_evidence"]["secrets_scrubbed"] is True
    assert runner.deleted is True


@pytest.mark.parametrize(
    ("runner", "code"),
    [
        (ScriptedRunner(download_fails=True), ExitCode.TRANSPORT_FAILED),
        (ScriptedRunner(delete_fails=True), ExitCode.SANDBOX_DELETE_FAILED),
    ],
)
def test_openshell_transport_and_delete_failures_never_sign(tmp_path, runner, code):
    with pytest.raises(SandboxExecutorError) as raised:
        execute_case_to_bundle(
            case=case(),
            profile=profile(),
            policy_path=POLICY_PATH,
            runner_path=RUNNER_PATH,
            driver=OpenShell059Driver(runner=runner),
            private_key=Ed25519PrivateKey.generate(),
            issuer_key_id="dev-test-key",
            output_dir=tmp_path / "bundle",
            benchmark_suite_digest=BENCHMARK_DIGEST,
            skill_artifact_digest=SKILL_DIGEST,
            run_id="run-00000001",
        )
    assert raised.value.code == code
    assert not (tmp_path / "bundle").exists()
    if code == ExitCode.TRANSPORT_FAILED:
        assert runner.deleted is True


def test_create_command_is_noninteractive_and_kept_for_evidence() -> None:
    request = ExecutionRequest(
        sandbox_name="arena-smoke-python-abcdef123456",
        workspace_nonce="c" * 32,
        case=case(),
        profile=profile(),
        policy_path=POLICY_PATH,
        runner_path=RUNNER_PATH,
    )
    command = OpenShell059Driver().build_create_command(
        request,
        Path("/tmp/case.json"),
    )
    assert command[:3] == ("openshell", "sandbox", "create")
    assert "--no-auto-providers" in command
    assert "--no-tty" in command
    assert "--no-keep" not in command
