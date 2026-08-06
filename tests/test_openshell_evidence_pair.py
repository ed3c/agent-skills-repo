from __future__ import annotations

import base64
import shutil
import subprocess
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jsonschema import Draft202012Validator, FormatChecker

from skill_arena.sandbox_executor import (
    ATTESTATION_SCHEMA,
    TASK_RESULT_SCHEMA,
    DriverOutcome,
    EvidencePairError,
    ExecutionRequest,
    SandboxCase,
    SandboxProfile,
    audit_private_key_absent,
    command_digest,
    execute_case_to_bundle,
    load_json_object,
    load_public_key,
    sha256_bytes,
    verify_evidence_pair,
)

ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = ROOT / "data/sandbox_cases/smoke-python.json"
PROFILE_PATH = ROOT / "data/sandbox_profiles/openshell-0.0.59-docker.json"
POLICY_PATH = ROOT / "data/sandbox_profiles/no-network.policy.yaml"
RUNNER_PATH = ROOT / "scripts/sandbox_case_runner.py"
SCHEMA_PATH = ROOT / "contracts/openshell-physical-evidence-pair.schema.json"
BENCHMARK_DIGEST = "sha256:" + "1" * 64
SKILL_DIGEST = "sha256:" + "2" * 64
IMAGE_DIGEST = "sha256:" + "3" * 64
POLICY_DIGEST = "sha256:" + "4" * 64
NOW = datetime(2026, 8, 6, 12, 0, 5, tzinfo=timezone.utc)


def case() -> SandboxCase:
    return SandboxCase.from_mapping(load_json_object(CASE_PATH))


def profile() -> SandboxProfile:
    return SandboxProfile.from_mapping(load_json_object(PROFILE_PATH))


class FakePhysicalDriver:
    def __init__(self) -> None:
        self.requests: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest) -> DriverOutcome:
        self.requests.append(request)
        started = NOW - timedelta(seconds=2)
        completed = NOW - timedelta(seconds=1)
        stdout = b"arena-smoke-v1\n"
        result = {
            "schema_version": TASK_RESULT_SCHEMA,
            "workspace_nonce": request.workspace_nonce,
            "command_digest": command_digest(request.case.command),
            "exit_code": 0,
            "stdout_base64": base64.b64encode(stdout).decode("ascii"),
            "stderr_base64": "",
            "stdout_sha256": sha256_bytes(stdout),
            "stderr_sha256": sha256_bytes(b""),
            "timed_out": False,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
        }
        control_evidence = {
            "openshell_version_stdout_sha256": "sha256:" + "5" * 64,
            "gateway_status_stdout_sha256": "sha256:" + "6" * 64,
            "docker_server_version": "27.5.1",
            "runner_bytes_sha256": "sha256:" + "7" * 64,
            "requested_policy_bytes_sha256": "sha256:" + "8" * 64,
            "resource_enforcement": "openshell-limits+runner-rlimit@1",
            "secrets_scrubbed": True,
            "auto_providers_disabled": True,
            "runner_input_bytes_sha256": "sha256:" + "9" * 64,
            "create_stdout_sha256": "sha256:" + "a" * 64,
            "effective_policy_bytes_sha256": POLICY_DIGEST,
            "docker_container_inspect_sha256": "sha256:" + "b" * 64,
            "docker_container_id": f"container-{request.sandbox_name}",
            "docker_memory_bytes": request.profile.resource_limits.memory_bytes,
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
            "cleanup_verified": True,
            "workspace_destroyed": True,
            "started_at": started.isoformat(),
            "completed_at": completed.isoformat(),
            "control_evidence": control_evidence,
        }
        return DriverOutcome(
            result=result,
            attestation=attestation,
            sandbox_image_digest=IMAGE_DIGEST,
            sandbox_policy_digest=POLICY_DIGEST,
            started_at=started,
            completed_at=completed,
            cleanup_verified=True,
        )


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def initialize_repo(root: Path) -> Path:
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "evidence@example.invalid")
    git(root, "config", "user.name", "Evidence Test")
    (root / "README.md").write_text("physical evidence audit fixture\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture")
    return root


def key_files(tmp_path: Path) -> tuple[Ed25519PrivateKey, Path, Path]:
    key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "dev-private.key"
    public_path = tmp_path / "dev-public.key"
    private_path.write_bytes(key.private_bytes_raw())
    private_path.chmod(0o600)
    public_path.write_bytes(key.public_key().public_bytes_raw())
    return key, private_path, public_path


def create_pair(tmp_path: Path) -> dict[str, object]:
    key, private_path, public_path = key_files(tmp_path)
    repository = initialize_repo(tmp_path / "repo")
    driver = FakePhysicalDriver()
    bundles: list[Path] = []
    for index in (1, 2):
        destination = tmp_path / f"bundle-{index}"
        execute_case_to_bundle(
            case=case(),
            profile=profile(),
            policy_path=POLICY_PATH,
            runner_path=RUNNER_PATH,
            driver=driver,
            private_key=key,
            issuer_key_id="dev-pair-test",
            output_dir=destination,
            benchmark_suite_digest=BENCHMARK_DIGEST,
            skill_artifact_digest=SKILL_DIGEST,
            now=NOW,
            run_id=f"run-0000000{index}",
        )
        bundles.append(destination)
    index = verify_evidence_pair(
        bundles,
        case=case(),
        profile=profile(),
        public_key=load_public_key(public_path),
        private_key_path=private_path,
        issuer_key_id="dev-pair-test",
        benchmark_suite_digest=BENCHMARK_DIGEST,
        skill_artifact_digest=SKILL_DIGEST,
        repo_root=repository,
        generated_at=NOW,
    )
    return {
        "index": index,
        "bundles": bundles,
        "key": key,
        "private_path": private_path,
        "public_path": public_path,
        "repository": repository,
    }


def verify_again(generated: dict[str, object], *, generated_at=None):
    return verify_evidence_pair(
        generated["bundles"],
        case=case(),
        profile=profile(),
        public_key=load_public_key(generated["public_path"]),
        private_key_path=generated["private_path"],
        issuer_key_id="dev-pair-test",
        benchmark_suite_digest=BENCHMARK_DIGEST,
        skill_artifact_digest=SKILL_DIGEST,
        repo_root=generated["repository"],
        generated_at=generated_at,
    )


def test_pair_is_admitted_reproducible_and_matches_schema(tmp_path: Path) -> None:
    generated = create_pair(tmp_path)
    index = generated["index"]
    schema = load_json_object(SCHEMA_PATH)
    errors = list(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(index)
    )
    assert errors == []
    assert index["controls"] == {
        "receipt_admitted_at_issued_time": True,
        "tamper_rejected_for_both": True,
        "original_bundles_unchanged": True,
        "distinct_sandbox_names": True,
        "distinct_workspace_nonces": True,
        "distinct_receipt_ids": True,
        "distinct_container_ids": True,
        "same_image_digest": True,
        "same_policy_digest": True,
        "same_docker_server_version": True,
        "same_deterministic_result": True,
        "cleanup_verified_for_both": True,
        "workspace_destroyed_for_both": True,
        "private_key_history_absent": True,
        "scanned_git_blobs": 1,
        "scanned_worktree_files": 1,
    }
    assert index["execution_envelope"]["docker_server_version"] == "27.5.1"
    assert len({run["workspace_nonce"] for run in index["runs"]}) == 2
    assert len({run["sandbox_name"] for run in index["runs"]}) == 2
    assert len({run["docker_container_id"] for run in index["runs"]}) == 2
    assert index["runs"][0]["output_evidence_digest"] == case().expected_evidence_digest
    assert all("bundle_path" not in run for run in index["runs"])
    assert all("currently_unexpired" not in run for run in index["runs"])

    reproduced = verify_again(generated)
    assert reproduced["generated_at"] == NOW.isoformat()
    assert reproduced["pair_digest"] == index["pair_digest"]


def test_duplicate_bundle_cannot_impersonate_two_runs(tmp_path: Path) -> None:
    generated = create_pair(tmp_path)
    first = generated["bundles"][0]
    duplicate = tmp_path / "duplicate"
    shutil.copytree(first, duplicate)

    with pytest.raises(EvidencePairError, match="reused a sandbox name"):
        verify_evidence_pair(
            [first, duplicate],
            case=case(),
            profile=profile(),
            public_key=load_public_key(generated["public_path"]),
            private_key_path=generated["private_path"],
            issuer_key_id="dev-pair-test",
            benchmark_suite_digest=BENCHMARK_DIGEST,
            skill_artifact_digest=SKILL_DIGEST,
            repo_root=generated["repository"],
            generated_at=NOW,
        )


def test_bundle_byte_drift_is_rejected(tmp_path: Path) -> None:
    generated = create_pair(tmp_path)
    result_path = generated["bundles"][1] / "result.json"
    result_path.write_bytes(result_path.read_bytes() + b"\n")

    with pytest.raises(EvidencePairError, match="bundle file digest mismatch"):
        verify_again(generated, generated_at=NOW)


def test_bundle_extra_file_is_rejected(tmp_path: Path) -> None:
    generated = create_pair(tmp_path)
    (generated["bundles"][1] / "unbound.log").write_text("not in manifest\n")

    with pytest.raises(EvidencePairError, match="exactly the four contract files"):
        verify_again(generated, generated_at=NOW)


def test_public_key_must_match_audited_private_key(tmp_path: Path) -> None:
    generated = create_pair(tmp_path)
    other = Ed25519PrivateKey.generate().public_key()

    with pytest.raises(EvidencePairError, match="does not match"):
        verify_evidence_pair(
            generated["bundles"],
            case=case(),
            profile=profile(),
            public_key=other,
            private_key_path=generated["private_path"],
            issuer_key_id="dev-pair-test",
            benchmark_suite_digest=BENCHMARK_DIGEST,
            skill_artifact_digest=SKILL_DIGEST,
            repo_root=generated["repository"],
            generated_at=NOW,
        )


def test_committed_pkcs8_der_representation_is_rejected(tmp_path: Path) -> None:
    generated = create_pair(tmp_path)
    repository = generated["repository"]
    der = generated["key"].private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    (repository / "leak.txt").write_bytes(base64.b64encode(der) + b"\n")
    git(repository, "add", "leak.txt")
    git(repository, "commit", "-m", "leak")

    with pytest.raises(EvidencePairError, match="private key material appears"):
        audit_private_key_absent(repository, generated["private_path"])


def test_ignored_pem_representation_is_rejected(tmp_path: Path) -> None:
    generated = create_pair(tmp_path)
    repository = generated["repository"]
    (repository / ".gitignore").write_text("ignored-private.pem\n")
    git(repository, "add", ".gitignore")
    git(repository, "commit", "-m", "ignore fixture")
    pem = generated["key"].private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    (repository / "ignored-private.pem").write_bytes(pem)

    with pytest.raises(EvidencePairError, match="worktree file"):
        audit_private_key_absent(repository, generated["private_path"])
