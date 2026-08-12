from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request

from jsonschema import Draft202012Validator
import pytest
from scripts import check_arena_provider_preflight as checker_cli
from scripts import preflight_arena_provider as preflight_cli

from skill_arena.experiment.model import ExperimentError, sha256_json
from skill_arena.experiment.provider_policy import (
    LocalProviderPolicy,
    OllamaHttpProbe,
    load_provider_policy,
    load_provider_revocations,
    _preflight_provider as preflight_provider,
    validate_provider_attempt,
    validate_provider_preflight,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "data/arena/ollama-qwen3-4b-local.json"
REVOCATION_PATH = ROOT / "data/arena/provider-observation-revocations.json"
SCHEMA_PATH = ROOT / "contracts/arena-provider-policy.schema.json"
CLI_PATH = ROOT / "scripts/preflight_arena_provider.py"
CHECKER_PATH = ROOT / "scripts/check_arena_provider_preflight.py"
LANDED_RECEIPT_PATH = (
    ROOT / "data/verification_runs/ollama_qwen3_provider_preflight_2026-08-12.json"
)
LANDED_ATTEMPT_PATH = (
    ROOT / "data/verification_runs/ollama_qwen3_provider_attempt_2026-08-12.json"
)
NOW = datetime(2026, 8, 12, 8, 30, tzinfo=timezone.utc)
ATTEMPT_ID = "provider-preflight-test-001"


def passing_attempt(policy: LocalProviderPolicy, receipt: dict[str, object]) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "arena-provider-attempt@1",
        "attempt_id": ATTEMPT_ID,
        "policy_id": policy.policy_id,
        "policy_digest": policy.digest,
        "coordination_issue_url": policy.coordination_issue_url,
        "observer_kind": "local-owner-session",
        "observer_actor_id": policy.observation_actor_id,
        "observation_authority_id": policy.observation_authority_id,
        "observer_host_digest": "sha256:" + "1" * 64,
        "runtime_identity": "darwin-arm64-python-3.14.6",
        "revocation_registry_digest": load_provider_revocations(REVOCATION_PATH)[
            "registry_digest"
        ],
        "started_at": "2026-08-12T08:29:00Z",
        "completed_at": "2026-08-12T08:31:00Z",
        "status": "succeeded",
        "model_invocation_count": 1,
        "diagnostic": None,
        "receipt_digest": receipt["receipt_digest"],
    }
    return {**value, "attempt_digest": sha256_json(value)}


class PassingProviderProbe:
    def __init__(
        self,
        *,
        version: str = "0.15.5",
        model_digest: str = "359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7",
        completion: dict[str, object] | None = None,
    ) -> None:
        self.version = version
        self.model_digest = model_digest
        self.completion = completion

    def get_version(self, base_url: str, *, timeout: int) -> str:
        assert base_url == "http://127.0.0.1:11434/v1"
        assert timeout == 10
        return self.version

    def get_models(self, base_url: str, *, timeout: int) -> list[dict[str, object]]:
        assert base_url == "http://127.0.0.1:11434/v1"
        assert timeout == 10
        return [
            {
                "name": "qwen3:4b",
                "digest": self.model_digest,
            }
        ]

    def complete_with_tool(
        self, base_url: str, *, model: str, timeout: int
    ) -> dict[str, object]:
        assert base_url == "http://127.0.0.1:11434/v1"
        assert model == "qwen3:4b"
        assert timeout == 120
        return self.completion or {
            "model": "qwen3:4b",
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": "provider_probe",
                                    "arguments": '{"value":"PROVIDER_OK"}',
                                },
                            }
                        ]
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 24,
                "completion_tokens": 8,
                "total_tokens": 32,
            },
        }


def test_landed_local_provider_policy_pins_zero_cost_ollama_envelope() -> None:
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert list(Draft202012Validator(schema).iter_errors(raw)) == []

    policy = load_provider_policy(POLICY_PATH)
    assert policy.policy_id == "ollama-qwen3-4b-local@1"
    assert policy.provider_id == "ollama-local"
    assert policy.provider_version == "0.15.5"
    assert policy.host_base_url == "http://127.0.0.1:11434/v1"
    assert policy.sandbox_base_url == "http://host.docker.internal:11434/v1"
    assert policy.model_id == "qwen3:4b"
    assert policy.model_digest == (
        "sha256:359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7"
    )
    assert policy.benchflow_model == "vllm/qwen3:4b"
    assert policy.credential_kind == "none"
    assert policy.external_api_cost_microunits_max == 0
    assert policy.experiment_execution_authorized is False
    revocations = load_provider_revocations(REVOCATION_PATH)
    assert list(Draft202012Validator(schema).iter_errors(revocations)) == []


def test_provider_preflight_emits_sanitized_capability_receipt() -> None:
    policy = load_provider_policy(POLICY_PATH)

    receipt = preflight_provider(
        policy,
        probe=PassingProviderProbe(),
        checked_at=NOW,
        attempt_id=ATTEMPT_ID,
        before_model_invocation=lambda: None,
    )

    assert receipt["schema_version"] == "arena-provider-preflight@1"
    assert receipt["attempt_id"] == ATTEMPT_ID
    assert receipt["policy_digest"] == policy.digest
    assert receipt["checked_at"] == "2026-08-12T08:30:00Z"
    assert receipt["model_digest"] == policy.model_digest
    assert receipt["capabilities"] == {
        "chat_completions": True,
        "model_digest": True,
        "tool_calls": True,
        "usage": True,
    }
    assert receipt["token_usage"] == {
        "prompt_tokens": 24,
        "completion_tokens": 8,
        "total_tokens": 32,
    }
    assert receipt["credential_used"] is False
    assert receipt["external_api_cost_microunits"] == 0
    assert receipt["ready"] is True
    assert receipt["experiment_execution_authorized"] is False
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(receipt)) == []
    assert validate_provider_preflight(receipt, policy) == receipt
    assert set(receipt) == {
        "schema_version",
        "attempt_id",
        "policy_id",
        "policy_digest",
        "provider_id",
        "provider_version",
        "endpoint_kind",
        "base_url",
        "model_id",
        "model_digest",
        "checked_at",
        "capabilities",
        "token_usage",
        "credential_used",
        "external_api_cost_microunits",
        "ready",
        "experiment_execution_authorized",
        "receipt_digest",
    }


def test_provider_policy_rejects_string_execution_authority() -> None:
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    changed = deepcopy(raw)
    changed["experiment_execution_authorized"] = "false"

    with pytest.raises(ExperimentError, match="experiment_execution_authorized"):
        LocalProviderPolicy.from_mapping(changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("provider_id", "github-models-retired"),
        ("selection_status", "selected"),
        ("experiment_execution_authorized", True),
    ],
)
def test_provider_policy_rejects_retired_or_unauthorized_envelopes(
    field: str, value: object
) -> None:
    changed = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    changed[field] = value

    with pytest.raises(ExperimentError, match=f"{field} is unsupported"):
        LocalProviderPolicy.from_mapping(changed)


def test_provider_preflight_revalidates_policy_after_object_construction() -> None:
    policy = replace(
        load_provider_policy(POLICY_PATH),
        host_base_url="http://127.0.0.1:9999/v1",
    )

    with pytest.raises(ExperimentError, match="host_base_url is unsupported"):
        preflight_provider(
            policy,
            probe=PassingProviderProbe(),
            checked_at=NOW,
            attempt_id=ATTEMPT_ID,
            before_model_invocation=lambda: None,
        )


def test_ollama_http_probe_uses_bounded_version_model_and_tool_surfaces() -> None:
    class Response:
        def __init__(self, value: object) -> None:
            self.body = json.dumps(value).encode("utf-8")

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return self.body[:limit]

    responses = [
        Response({"version": "0.15.5"}),
        Response(
            {
                "models": [
                    {
                        "name": "qwen3:4b",
                        "digest": "359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7",
                    }
                ]
            }
        ),
        Response(
            {
                "model": "qwen3:4b",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "tool_calls": [
                                {
                                    "type": "function",
                                    "function": {
                                        "name": "provider_probe",
                                        "arguments": '{"value":"PROVIDER_OK"}',
                                    },
                                }
                            ]
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 4,
                    "total_tokens": 9,
                },
            }
        ),
    ]
    requests: list[tuple[Request, int]] = []

    def opener(request: Request, *, timeout: int) -> Response:
        requests.append((request, timeout))
        return responses.pop(0)

    base_url = "http://127.0.0.1:11434/v1"
    probe = OllamaHttpProbe(opener=opener)
    assert probe.get_version(base_url, timeout=10) == "0.15.5"
    assert probe.get_models(base_url, timeout=10)[0]["name"] == "qwen3:4b"
    completion = probe.complete_with_tool(base_url, model="qwen3:4b", timeout=120)
    assert completion["model"] == "qwen3:4b"

    assert [request.full_url for request, _ in requests] == [
        "http://127.0.0.1:11434/api/version",
        "http://127.0.0.1:11434/api/tags",
        "http://127.0.0.1:11434/v1/chat/completions",
    ]
    assert [timeout for _, timeout in requests] == [10, 10, 120]
    body = json.loads(requests[2][0].data or b"")
    assert isinstance(body, dict)
    assert body["stream"] is False
    assert body["think"] is False
    assert body["model"] == "qwen3:4b"
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "provider_probe",
                "description": "Return the fixed provider capability sentinel.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"value": {"const": "PROVIDER_OK"}},
                    "required": ["value"],
                },
            },
        }
    ]


@pytest.mark.parametrize(
    ("probe", "error"),
    [
        (PassingProviderProbe(version="0.15.4"), "version_mismatch"),
        (PassingProviderProbe(model_digest="0" * 64), "model_digest_mismatch"),
        (
            PassingProviderProbe(
                completion={
                    "model": "qwen3:4b",
                    "choices": [{"finish_reason": "stop", "message": {}}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            ),
            "tool_call_missing",
        ),
        (
            PassingProviderProbe(
                completion={
                    "model": "qwen3:4b",
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "tool_calls": [
                                    {
                                        "type": "function",
                                        "function": {
                                            "name": "provider_probe",
                                            "arguments": '{"value":"PROVIDER_OK"}',
                                        },
                                    }
                                ]
                            },
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 99,
                    },
                }
            ),
            "usage_total_mismatch",
        ),
    ],
)
def test_provider_preflight_rejects_capability_mismatches(
    probe: PassingProviderProbe, error: str
) -> None:
    with pytest.raises(ExperimentError, match=error):
        preflight_provider(
            load_provider_policy(POLICY_PATH),
            probe=probe,
            checked_at=NOW,
            attempt_id=ATTEMPT_ID,
            before_model_invocation=lambda: None,
        )


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            urllib.error.HTTPError(
                "http://127.0.0.1:11434/api/version",
                401,
                "Bearer SECRET_TOKEN",
                None,
                None,
            ),
            "provider_preflight_http_error status=401",
        ),
        (
            urllib.error.URLError("SECRET_TOKEN unavailable"),
            "provider_preflight_transport_error",
        ),
    ],
)
def test_ollama_http_probe_sanitizes_unavailable_or_unauthorized_failures(
    failure: Exception, expected: str
) -> None:
    def opener(request: Request, *, timeout: int) -> object:
        raise failure

    with pytest.raises(ExperimentError) as raised:
        OllamaHttpProbe(opener=opener).get_version(
            "http://127.0.0.1:11434/v1", timeout=10
        )

    assert str(raised.value) == expected
    assert "SECRET_TOKEN" not in str(raised.value)


def test_ollama_http_probe_rejects_provider_schema_drift() -> None:
    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return b'{"models":"SECRET_PROVIDER_BODY"}'

    with pytest.raises(ExperimentError) as raised:
        OllamaHttpProbe(opener=lambda request, timeout: Response()).get_models(
            "http://127.0.0.1:11434/v1", timeout=10
        )

    assert str(raised.value) == "provider_preflight_models_schema_invalid"
    assert "SECRET_PROVIDER_BODY" not in str(raised.value)


def test_provider_preflight_cli_validate_only_does_not_claim_readiness() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(CLI_PATH),
            "--policy",
            str(POLICY_PATH),
            "--validate-only",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result == {
        "experiment_execution_authorized": False,
        "policy_digest": load_provider_policy(POLICY_PATH).digest,
        "policy_id": "ollama-qwen3-4b-local@1",
        "provider_ready": False,
        "status": "policy-valid",
    }


def test_provider_preflight_cli_preserves_sanitized_failed_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "capability.json"
    attempt_path = tmp_path / "attempt.json"
    calls = 0

    def fail_preflight(
        policy: LocalProviderPolicy,
        *,
        probe: object,
        checked_at: datetime,
        attempt_id: str,
        before_model_invocation: object,
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        assert callable(before_model_invocation)
        before_model_invocation()
        raise ExperimentError("provider_preflight_transport_error")

    monkeypatch.setattr(preflight_cli, "_preflight_provider", fail_preflight)

    assert (
        preflight_cli.main(
            [
                "--policy",
                str(POLICY_PATH),
                "--output",
                str(output_path),
                "--attempt-receipt",
                str(attempt_path),
            ]
        )
        == 2
    )
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    assert attempt["status"] == "failed"
    assert attempt["model_invocation_count"] == 1
    assert attempt["diagnostic"] == "provider_preflight_transport_error"
    assert attempt["receipt_digest"] is None
    assert not output_path.exists()
    assert calls == 1
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(attempt)) == []
    assert preflight_cli.main(
        [
            "--policy",
            str(POLICY_PATH),
            "--output",
            str(output_path),
            "--attempt-receipt",
            str(attempt_path),
        ]
    ) == 2
    assert calls == 1


def test_provider_preflight_cli_rejects_existing_output_before_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "capability.json"
    attempt_path = tmp_path / "attempt.json"
    output_path.write_text("do-not-overwrite\n", encoding="utf-8")
    calls = 0

    def unexpected_preflight(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise AssertionError("probe must not run")

    monkeypatch.setattr(preflight_cli, "_preflight_provider", unexpected_preflight)

    assert preflight_cli.main(
        [
            "--policy",
            str(POLICY_PATH),
            "--output",
            str(output_path),
            "--attempt-receipt",
            str(attempt_path),
        ]
    ) == 2
    assert calls == 0
    assert output_path.read_text(encoding="utf-8") == "do-not-overwrite\n"
    assert not attempt_path.exists()


def test_provider_preflight_cli_finalizes_failure_when_output_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "capability.json"
    attempt_path = tmp_path / "attempt.json"
    real_write = preflight_cli._write_exclusive

    def selective_write(path: Path, value: object) -> None:
        if path == output_path:
            raise ExperimentError("cannot write provider preflight output: sanitized")
        real_write(path, value)

    monkeypatch.setattr(preflight_cli, "_write_exclusive", selective_write)
    monkeypatch.setattr(
        preflight_cli,
        "_preflight_provider",
        lambda policy, *, probe, checked_at, attempt_id, before_model_invocation: (
            before_model_invocation(),
            preflight_provider(
                policy,
                probe=PassingProviderProbe(),
                checked_at=NOW,
                attempt_id=attempt_id,
                before_model_invocation=lambda: None,
            ),
        )[1],
    )

    assert preflight_cli.main(
        [
            "--policy",
            str(POLICY_PATH),
            "--output",
            str(output_path),
            "--attempt-receipt",
            str(attempt_path),
        ]
    ) == 2
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    assert attempt["status"] == "failed"
    assert attempt["model_invocation_count"] == 1
    assert attempt["diagnostic"] == (
        "cannot write provider preflight output: sanitized"
    )
    assert not output_path.exists()
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(attempt)) == []


def test_provider_preflight_checker_verifies_receipt_offline(tmp_path: Path) -> None:
    policy = load_provider_policy(POLICY_PATH)
    receipt = preflight_provider(
        policy,
        probe=PassingProviderProbe(),
        checked_at=NOW,
        attempt_id=ATTEMPT_ID,
        before_model_invocation=lambda: None,
    )
    receipt_path = tmp_path / "provider-preflight.json"
    receipt_path.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    attempt = passing_attempt(policy, receipt)
    attempt_path = tmp_path / "provider-attempt.json"
    attempt_path.write_text(
        json.dumps(attempt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(CHECKER_PATH),
            "--policy",
            str(POLICY_PATH),
            "--receipt",
            str(receipt_path),
            "--attempt-receipt",
            str(attempt_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout) == {
        "checked_at": "2026-08-12T08:30:00Z",
        "attempt_digest": attempt["attempt_digest"],
        "experiment_execution_authorized": False,
        "observed_ready": True,
        "policy_digest": policy.digest,
        "receipt_verified": True,
        "receipt_digest": receipt["receipt_digest"],
        "status": "verified",
    }


def test_landed_provider_preflight_receipt_verifies_offline() -> None:
    policy = load_provider_policy(POLICY_PATH)
    receipt = json.loads(LANDED_RECEIPT_PATH.read_text(encoding="utf-8"))

    attempt = json.loads(LANDED_ATTEMPT_PATH.read_text(encoding="utf-8"))
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert list(Draft202012Validator(schema).iter_errors(receipt)) == []
    assert list(Draft202012Validator(schema).iter_errors(attempt)) == []
    assert validate_provider_preflight(receipt, policy) == receipt
    assert validate_provider_attempt(
        attempt, policy, receipt, load_provider_revocations(REVOCATION_PATH)
    ) == attempt


def test_provider_preflight_receipt_rejects_digest_tampering() -> None:
    policy = load_provider_policy(POLICY_PATH)
    receipt = preflight_provider(
        policy,
        probe=PassingProviderProbe(),
        checked_at=NOW,
        attempt_id=ATTEMPT_ID,
        before_model_invocation=lambda: None,
    )
    receipt["checked_at"] = "2026-08-12T08:31:00Z"

    with pytest.raises(ExperimentError, match="receipt digest mismatch"):
        validate_provider_preflight(receipt, policy)


def test_provider_attempt_rejects_impossible_timeline_after_redigest() -> None:
    policy = load_provider_policy(POLICY_PATH)
    receipt = preflight_provider(
        policy,
        probe=PassingProviderProbe(),
        checked_at=NOW,
        attempt_id=ATTEMPT_ID,
        before_model_invocation=lambda: None,
    )
    attempt = passing_attempt(policy, receipt)
    attempt["completed_at"] = "2026-08-12T08:00:00Z"
    without_digest = {
        key: value for key, value in attempt.items() if key != "attempt_digest"
    }
    attempt["attempt_digest"] = sha256_json(without_digest)

    with pytest.raises(ExperimentError, match="timeline mismatch"):
        validate_provider_attempt(
            attempt, policy, receipt, load_provider_revocations(REVOCATION_PATH)
        )


def test_provider_attempt_verifies_terminal_failure_without_capability_receipt(
    tmp_path: Path,
) -> None:
    policy = load_provider_policy(POLICY_PATH)
    receipt = preflight_provider(
        policy,
        probe=PassingProviderProbe(),
        checked_at=NOW,
        attempt_id=ATTEMPT_ID,
        before_model_invocation=lambda: None,
    )
    attempt = passing_attempt(policy, receipt)
    attempt.update(
        {
            "status": "failed",
            "diagnostic": "provider_preflight_transport_error",
            "receipt_digest": None,
        }
    )
    without_digest = {
        key: value for key, value in attempt.items() if key != "attempt_digest"
    }
    attempt["attempt_digest"] = sha256_json(without_digest)
    revocations = load_provider_revocations(REVOCATION_PATH)

    assert validate_provider_attempt(attempt, policy, None, revocations) == attempt
    attempt_path = tmp_path / "failed-attempt.json"
    attempt_path.write_text(
        json.dumps(attempt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    completed = subprocess.run(
        [
            sys.executable,
            str(CHECKER_PATH),
            "--policy",
            str(POLICY_PATH),
            "--attempt-receipt",
            str(attempt_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["status"] == "verified-failed-attempt"


def test_provider_attempt_rejects_revoked_observation() -> None:
    policy = load_provider_policy(POLICY_PATH)
    receipt = preflight_provider(
        policy,
        probe=PassingProviderProbe(),
        checked_at=NOW,
        attempt_id=ATTEMPT_ID,
        before_model_invocation=lambda: None,
    )
    attempt = passing_attempt(policy, receipt)
    revocations = load_provider_revocations(REVOCATION_PATH)
    revocations["revoked_attempt_ids"] = [ATTEMPT_ID]
    registry_without_digest = {
        key: value for key, value in revocations.items() if key != "registry_digest"
    }
    revocations["registry_digest"] = sha256_json(registry_without_digest)

    with pytest.raises(ExperimentError, match="is revoked"):
        validate_provider_attempt(attempt, policy, receipt, revocations)


def test_unrelated_current_registry_update_keeps_historical_attempt_valid() -> None:
    policy = load_provider_policy(POLICY_PATH)
    receipt = preflight_provider(
        policy,
        probe=PassingProviderProbe(),
        checked_at=NOW,
        attempt_id=ATTEMPT_ID,
        before_model_invocation=lambda: None,
    )
    attempt = passing_attempt(policy, receipt)
    mint_digest = attempt["revocation_registry_digest"]
    current = load_provider_revocations(REVOCATION_PATH)
    current["revoked_attempt_ids"] = ["different-attempt"]
    registry_without_digest = {
        key: value for key, value in current.items() if key != "registry_digest"
    }
    current["registry_digest"] = sha256_json(registry_without_digest)

    assert current["registry_digest"] != mint_digest
    assert validate_provider_attempt(attempt, policy, receipt, current) == attempt


def test_checker_does_not_accept_caller_selected_revocation_registry() -> None:
    for command_parser, remaining in (
        (preflight_cli.parser(), ["--validate-only"]),
        (checker_cli.parser(), ["--attempt-receipt", "attempt.json"]),
    ):
        with pytest.raises(SystemExit):
            command_parser.parse_args(
                [
                    "--policy",
                    str(POLICY_PATH),
                    "--revocation-registry",
                    str(REVOCATION_PATH),
                    *remaining,
                ]
            )
