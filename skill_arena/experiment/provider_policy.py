"""Fail-closed policy for one local Arena model provider envelope."""

from __future__ import annotations

import copy
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

from .model import ExperimentError, require_sha256, sha256_json, timestamp


POLICY_SCHEMA = "arena-provider-policy@1"
_POLICY_FIELDS = {
    "schema_version",
    "policy_id",
    "selection_status",
    "provider_id",
    "provider_software",
    "provider_version",
    "endpoint_kind",
    "host_base_url",
    "sandbox_base_url",
    "network_scope",
    "model_id",
    "model_digest",
    "model_family",
    "model_parameter_size",
    "model_quantization",
    "model_license",
    "benchflow_version",
    "benchflow_agent",
    "benchflow_model",
    "sandbox",
    "credential_kind",
    "credential_env",
    "external_api_cost_microunits_max",
    "capability_requirements",
    "max_capability_probe_invocations",
    "experiment_execution_authorized",
    "observation_actor_id",
    "observation_authority_id",
    "revocation_registry_path",
    "coordination_issue_url",
    "selection_rationale",
}
_CAPABILITIES = (
    "chat-completions",
    "model-digest",
    "tool-calls",
    "usage",
)
_RECEIPT_FIELDS = {
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
_ATTEMPT_FIELDS = {
    "schema_version",
    "attempt_id",
    "policy_id",
    "policy_digest",
    "coordination_issue_url",
    "observer_kind",
    "observer_actor_id",
    "observation_authority_id",
    "revocation_registry_digest",
    "observer_host_digest",
    "runtime_identity",
    "started_at",
    "completed_at",
    "status",
    "model_invocation_count",
    "diagnostic",
    "receipt_digest",
    "attempt_digest",
}
_REVOCATION_FIELDS = {
    "schema_version",
    "registry_id",
    "actor_id",
    "authority_id",
    "updated_at",
    "revoked_attempt_ids",
    "superseded_attempt_ids",
    "registry_digest",
}


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExperimentError(f"provider policy {label} must be a non-empty string")
    return value


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise ExperimentError(f"provider policy {label} must be an integer")
    return value


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ExperimentError(f"provider policy {label} must be a boolean")
    return value


@dataclass(frozen=True)
class LocalProviderPolicy:
    policy_id: str
    selection_status: str
    provider_id: str
    provider_software: str
    provider_version: str
    endpoint_kind: str
    host_base_url: str
    sandbox_base_url: str
    network_scope: str
    model_id: str
    model_digest: str
    model_family: str
    model_parameter_size: str
    model_quantization: str
    model_license: str
    benchflow_version: str
    benchflow_agent: str
    benchflow_model: str
    sandbox: str
    credential_kind: str
    credential_env: None
    external_api_cost_microunits_max: int
    capability_requirements: tuple[str, ...]
    max_capability_probe_invocations: int
    experiment_execution_authorized: bool
    observation_actor_id: str
    observation_authority_id: str
    revocation_registry_path: str
    coordination_issue_url: str
    selection_rationale: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "LocalProviderPolicy":
        if set(raw) != _POLICY_FIELDS or raw.get("schema_version") != POLICY_SCHEMA:
            raise ExperimentError("provider policy schema or fields are invalid")
        capabilities = raw.get("capability_requirements")
        if capabilities != list(_CAPABILITIES):
            raise ExperimentError("provider policy capability requirements are unsupported")
        policy = cls(
            policy_id=_string(raw.get("policy_id"), "policy_id"),
            selection_status=_string(raw.get("selection_status"), "selection_status"),
            provider_id=_string(raw.get("provider_id"), "provider_id"),
            provider_software=_string(raw.get("provider_software"), "provider_software"),
            provider_version=_string(raw.get("provider_version"), "provider_version"),
            endpoint_kind=_string(raw.get("endpoint_kind"), "endpoint_kind"),
            host_base_url=_string(raw.get("host_base_url"), "host_base_url"),
            sandbox_base_url=_string(raw.get("sandbox_base_url"), "sandbox_base_url"),
            network_scope=_string(raw.get("network_scope"), "network_scope"),
            model_id=_string(raw.get("model_id"), "model_id"),
            model_digest=require_sha256(raw.get("model_digest"), "provider model_digest"),
            model_family=_string(raw.get("model_family"), "model_family"),
            model_parameter_size=_string(
                raw.get("model_parameter_size"), "model_parameter_size"
            ),
            model_quantization=_string(
                raw.get("model_quantization"), "model_quantization"
            ),
            model_license=_string(raw.get("model_license"), "model_license"),
            benchflow_version=_string(
                raw.get("benchflow_version"), "benchflow_version"
            ),
            benchflow_agent=_string(raw.get("benchflow_agent"), "benchflow_agent"),
            benchflow_model=_string(raw.get("benchflow_model"), "benchflow_model"),
            sandbox=_string(raw.get("sandbox"), "sandbox"),
            credential_kind=_string(raw.get("credential_kind"), "credential_kind"),
            credential_env=None,
            external_api_cost_microunits_max=_integer(
                raw.get("external_api_cost_microunits_max"),
                "external_api_cost_microunits_max",
            ),
            capability_requirements=_CAPABILITIES,
            max_capability_probe_invocations=_integer(
                raw.get("max_capability_probe_invocations"),
                "max_capability_probe_invocations",
            ),
            experiment_execution_authorized=_boolean(
                raw.get("experiment_execution_authorized"),
                "experiment_execution_authorized",
            ),
            observation_actor_id=_string(
                raw.get("observation_actor_id"), "observation_actor_id"
            ),
            observation_authority_id=_string(
                raw.get("observation_authority_id"), "observation_authority_id"
            ),
            revocation_registry_path=_string(
                raw.get("revocation_registry_path"), "revocation_registry_path"
            ),
            coordination_issue_url=_string(
                raw.get("coordination_issue_url"), "coordination_issue_url"
            ),
            selection_rationale=_string(
                raw.get("selection_rationale"), "selection_rationale"
            ),
        )
        if raw.get("credential_env") is not None:
            raise ExperimentError("local provider policy must not name a credential env")
        expected = {
            "policy_id": "ollama-qwen3-4b-local@1",
            "selection_status": "proposed",
            "provider_id": "ollama-local",
            "provider_software": "ollama",
            "provider_version": "0.15.5",
            "endpoint_kind": "openai-chat-completions",
            "host_base_url": "http://127.0.0.1:11434/v1",
            "sandbox_base_url": "http://host.docker.internal:11434/v1",
            "network_scope": "local-machine-only",
            "model_id": "qwen3:4b",
            "model_digest": "sha256:359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7",
            "model_family": "qwen3",
            "model_parameter_size": "4.0B",
            "model_quantization": "Q4_K_M",
            "model_license": "Apache-2.0",
            "benchflow_version": "0.6.3",
            "benchflow_agent": "pi-acp",
            "benchflow_model": "vllm/qwen3:4b",
            "sandbox": "docker",
            "credential_kind": "none",
            "external_api_cost_microunits_max": 0,
            "max_capability_probe_invocations": 1,
            "experiment_execution_authorized": False,
            "observation_actor_id": "github:ed3c",
            "observation_authority_id": "repository-owner-review@1",
            "revocation_registry_path": "data/arena/provider-observation-revocations.json",
            "coordination_issue_url": "https://github.com/ed3c/agent-skills-repo/issues/46",
        }
        for field, value in expected.items():
            if getattr(policy, field) != value:
                raise ExperimentError(f"provider policy {field} is unsupported")
        return policy

    @property
    def digest(self) -> str:
        return sha256_json(self.as_mapping())

    def as_mapping(self) -> dict[str, object]:
        return {
            "schema_version": POLICY_SCHEMA,
            **{
                field: getattr(self, field)
                for field in _POLICY_FIELDS
                if field not in {"schema_version", "capability_requirements"}
            },
            "capability_requirements": list(self.capability_requirements),
        }


def load_provider_policy(path: Path | str) -> LocalProviderPolicy:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read provider policy: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExperimentError("provider policy root must be an object")
    return LocalProviderPolicy.from_mapping(value)


def validate_provider_revocations(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != _REVOCATION_FIELDS:
        raise ExperimentError("provider revocation registry fields are invalid")
    expected = {
        "schema_version": "arena-provider-observation-revocations@1",
        "registry_id": "local-provider-observation-revocations@1",
        "actor_id": "github:ed3c",
        "authority_id": "repository-owner-review@1",
    }
    for field, expected_value in expected.items():
        if value.get(field) != expected_value:
            raise ExperimentError(f"provider revocation registry {field} mismatch")
    timestamp(value.get("updated_at"), "provider revocation registry updated_at")
    for field in ("revoked_attempt_ids", "superseded_attempt_ids"):
        entries = value.get(field)
        if not isinstance(entries, list) or any(
            not isinstance(entry, str) or not entry for entry in entries
        ) or len(entries) != len(set(entries)):
            raise ExperimentError(f"provider revocation registry {field} invalid")
    stored_digest = require_sha256(
        value.get("registry_digest"), "provider revocation registry digest"
    )
    without_digest = {
        key: item for key, item in value.items() if key != "registry_digest"
    }
    if stored_digest != sha256_json(without_digest):
        raise ExperimentError("provider revocation registry digest mismatch")
    return copy.deepcopy(dict(value))


def load_provider_revocations(path: Path | str) -> dict[str, object]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ExperimentError("provider revocation registry is absent or unsafe")
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError("cannot read provider revocation registry") from exc
    if not isinstance(value, dict):
        raise ExperimentError("provider revocation registry root is invalid")
    return validate_provider_revocations(value)


class ProviderProbe(Protocol):
    """The external provider boundary used by the capability preflight."""

    def get_version(self, base_url: str, *, timeout: int) -> str: ...

    def get_models(
        self, base_url: str, *, timeout: int
    ) -> list[dict[str, object]]: ...

    def complete_with_tool(
        self, base_url: str, *, model: str, timeout: int
    ) -> dict[str, object]: ...


class OllamaHttpProbe:
    """Bounded Ollama/OpenAI-compatible client with sanitized failures."""

    _MAX_RESPONSE_BYTES = 1_048_576

    def __init__(self, opener: Callable[..., object] = urllib.request.urlopen) -> None:
        self._opener = opener

    @staticmethod
    def _urls(base_url: str) -> tuple[str, str, str]:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "host.docker.internal"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path.rstrip("/") != "/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ExperimentError("provider_preflight_base_url_unsafe")
        origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        return (
            origin + "/api/version",
            origin + "/api/tags",
            origin + "/v1/chat/completions",
        )

    def _request_json(
        self,
        url: str,
        *,
        timeout: int,
        payload: object | None = None,
    ) -> dict[str, object]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"} if body is not None else {},
            method="POST" if body is not None else "GET",
        )
        try:
            response_context = self._opener(request, timeout=timeout)
            with response_context as response:  # type: ignore[attr-defined]
                raw = response.read(self._MAX_RESPONSE_BYTES + 1)  # type: ignore[attr-defined]
        except urllib.error.HTTPError as exc:
            raise ExperimentError(
                f"provider_preflight_http_error status={exc.code}"
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ExperimentError("provider_preflight_transport_error") from exc
        if len(raw) > self._MAX_RESPONSE_BYTES:
            raise ExperimentError("provider_preflight_response_too_large")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExperimentError("provider_preflight_response_invalid_json") from exc
        if not isinstance(value, dict):
            raise ExperimentError("provider_preflight_response_root_invalid")
        return value

    def get_version(self, base_url: str, *, timeout: int) -> str:
        version_url, _, _ = self._urls(base_url)
        value = self._request_json(version_url, timeout=timeout)
        if set(value) != {"version"} or not isinstance(value.get("version"), str):
            raise ExperimentError("provider_preflight_version_schema_invalid")
        return str(value["version"])

    def get_models(
        self, base_url: str, *, timeout: int
    ) -> list[dict[str, object]]:
        _, tags_url, _ = self._urls(base_url)
        value = self._request_json(tags_url, timeout=timeout)
        models = value.get("models")
        if not isinstance(models, list):
            raise ExperimentError("provider_preflight_models_schema_invalid")
        sanitized: list[dict[str, object]] = []
        for item in models:
            if not isinstance(item, Mapping):
                raise ExperimentError("provider_preflight_models_schema_invalid")
            name = item.get("name")
            digest = item.get("digest")
            if not isinstance(name, str) or not isinstance(digest, str):
                raise ExperimentError("provider_preflight_models_schema_invalid")
            sanitized.append({"name": name, "digest": digest})
        return sanitized

    def complete_with_tool(
        self, base_url: str, *, model: str, timeout: int
    ) -> dict[str, object]:
        _, _, completion_url = self._urls(base_url)
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Call provider_probe once with value PROVIDER_OK. "
                        "Do not answer with prose."
                    ),
                }
            ],
            "tools": [
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
            ],
            "temperature": 0,
            "stream": False,
            "think": False,
        }
        return self._request_json(completion_url, timeout=timeout, payload=payload)


def _probe_usage(value: object) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise ExperimentError("provider_preflight_usage_missing")
    usage: dict[str, int] = {}
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        raw = value.get(name)
        if type(raw) is not int or raw < 0:
            raise ExperimentError("provider_preflight_usage_invalid")
        usage[name] = raw
    if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]:
        raise ExperimentError("provider_preflight_usage_total_mismatch")
    return usage


def _probe_tool_call(value: object, model_id: str) -> dict[str, int]:
    if not isinstance(value, Mapping) or value.get("model") != model_id:
        raise ExperimentError("provider_preflight_completion_model_mismatch")
    choices = value.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ExperimentError("provider_preflight_choice_cardinality")
    choice = choices[0]
    if not isinstance(choice, Mapping) or choice.get("finish_reason") != "tool_calls":
        raise ExperimentError("provider_preflight_tool_call_missing")
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise ExperimentError("provider_preflight_tool_call_missing")
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ExperimentError("provider_preflight_tool_call_cardinality")
    call = calls[0]
    if not isinstance(call, Mapping) or call.get("type") != "function":
        raise ExperimentError("provider_preflight_tool_call_invalid")
    function = call.get("function")
    if not isinstance(function, Mapping) or function.get("name") != "provider_probe":
        raise ExperimentError("provider_preflight_tool_call_invalid")
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise ExperimentError("provider_preflight_tool_call_invalid")
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as exc:
        raise ExperimentError("provider_preflight_tool_call_invalid") from exc
    if parsed != {"value": "PROVIDER_OK"}:
        raise ExperimentError("provider_preflight_tool_call_invalid")
    return _probe_usage(value.get("usage"))


def _preflight_provider(
    policy: LocalProviderPolicy,
    *,
    probe: ProviderProbe,
    checked_at: datetime,
    attempt_id: str,
    before_model_invocation: Callable[[], None],
) -> dict[str, object]:
    """Probe one bounded local provider request and emit no provider body text."""

    policy = LocalProviderPolicy.from_mapping(policy.as_mapping())
    if not attempt_id or any(character.isspace() for character in attempt_id):
        raise ExperimentError("provider preflight attempt_id is invalid")
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ExperimentError("provider preflight checked_at must include a timezone")
    if policy.max_capability_probe_invocations != 1:
        raise ExperimentError("provider capability probe must allow exactly one invocation")
    version = probe.get_version(policy.host_base_url, timeout=10)
    if version != policy.provider_version:
        raise ExperimentError("provider_preflight_version_mismatch")
    models = probe.get_models(policy.host_base_url, timeout=10)
    matches = [item for item in models if item.get("name") == policy.model_id]
    if len(matches) != 1:
        raise ExperimentError("provider_preflight_model_cardinality")
    digest = matches[0].get("digest")
    if digest != policy.model_digest.removeprefix("sha256:"):
        raise ExperimentError("provider_preflight_model_digest_mismatch")
    before_model_invocation()
    completion = probe.complete_with_tool(
        policy.host_base_url,
        model=policy.model_id,
        timeout=120,
    )
    usage = _probe_tool_call(completion, policy.model_id)
    without_digest: dict[str, object] = {
        "schema_version": "arena-provider-preflight@1",
        "attempt_id": attempt_id,
        "policy_id": policy.policy_id,
        "policy_digest": policy.digest,
        "provider_id": policy.provider_id,
        "provider_version": version,
        "endpoint_kind": policy.endpoint_kind,
        "base_url": policy.host_base_url,
        "model_id": policy.model_id,
        "model_digest": policy.model_digest,
        "checked_at": checked_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "capabilities": {
            "chat_completions": True,
            "model_digest": True,
            "tool_calls": True,
            "usage": True,
        },
        "token_usage": usage,
        "credential_used": False,
        "external_api_cost_microunits": 0,
        "ready": True,
        "experiment_execution_authorized": policy.experiment_execution_authorized,
    }
    return {
        **without_digest,
        "receipt_digest": sha256_json(without_digest),
    }


def validate_provider_preflight(
    receipt: Mapping[str, object],
    policy: LocalProviderPolicy,
) -> dict[str, object]:
    """Verify one capability receipt without provider or credential access."""

    if set(receipt) != _RECEIPT_FIELDS:
        raise ExperimentError("provider preflight receipt fields are invalid")
    expected = {
        "schema_version": "arena-provider-preflight@1",
        "policy_id": policy.policy_id,
        "policy_digest": policy.digest,
        "provider_id": policy.provider_id,
        "provider_version": policy.provider_version,
        "endpoint_kind": policy.endpoint_kind,
        "base_url": policy.host_base_url,
        "model_id": policy.model_id,
        "model_digest": policy.model_digest,
        "credential_used": False,
        "external_api_cost_microunits": 0,
        "ready": True,
        "experiment_execution_authorized": policy.experiment_execution_authorized,
    }
    attempt_id = receipt.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id or any(
        character.isspace() for character in attempt_id
    ):
        raise ExperimentError("provider preflight receipt attempt_id mismatch")
    for field, value in expected.items():
        if receipt.get(field) != value or type(receipt.get(field)) is not type(value):
            raise ExperimentError(f"provider preflight receipt {field} mismatch")
    timestamp(receipt.get("checked_at"), "provider preflight checked_at")
    capabilities = receipt.get("capabilities")
    if capabilities != {
        "chat_completions": True,
        "model_digest": True,
        "tool_calls": True,
        "usage": True,
    }:
        raise ExperimentError("provider preflight capabilities mismatch")
    _probe_usage(receipt.get("token_usage"))
    stored_digest = require_sha256(
        receipt.get("receipt_digest"), "provider preflight receipt_digest"
    )
    without_digest = {
        key: value for key, value in receipt.items() if key != "receipt_digest"
    }
    if stored_digest != sha256_json(without_digest):
        raise ExperimentError("provider preflight receipt digest mismatch")
    return copy.deepcopy(dict(receipt))


def validate_provider_attempt(
    attempt: Mapping[str, object],
    policy: LocalProviderPolicy,
    receipt: Mapping[str, object] | None,
    revocations: Mapping[str, object],
) -> dict[str, object]:
    """Verify one terminal success or failure denominator entry."""

    revocations = validate_provider_revocations(revocations)
    if set(attempt) != _ATTEMPT_FIELDS:
        raise ExperimentError("provider attempt receipt fields are invalid")
    expected: dict[str, object] = {
        "schema_version": "arena-provider-attempt@1",
        "policy_id": policy.policy_id,
        "policy_digest": policy.digest,
        "coordination_issue_url": policy.coordination_issue_url,
        "observer_kind": "local-owner-session",
        "observer_actor_id": policy.observation_actor_id,
        "observation_authority_id": policy.observation_authority_id,
    }
    for field, value in expected.items():
        if attempt.get(field) != value or type(attempt.get(field)) is not type(value):
            raise ExperimentError(f"provider attempt receipt {field} mismatch")
    require_sha256(attempt.get("observer_host_digest"), "observer_host_digest")
    require_sha256(
        attempt.get("revocation_registry_digest"), "revocation_registry_digest"
    )
    _string(attempt.get("runtime_identity"), "attempt runtime_identity")
    timestamp(attempt.get("started_at"), "provider attempt started_at")
    timestamp(attempt.get("completed_at"), "provider attempt completed_at")
    started = datetime.fromisoformat(str(attempt["started_at"]).replace("Z", "+00:00"))
    completed = datetime.fromisoformat(
        str(attempt["completed_at"]).replace("Z", "+00:00")
    )
    if completed < started:
        raise ExperimentError("provider attempt receipt timeline mismatch")
    attempt_id = attempt.get("attempt_id")
    if attempt_id in revocations.get("revoked_attempt_ids", []):
        raise ExperimentError("provider attempt receipt is revoked")
    if attempt_id in revocations.get("superseded_attempt_ids", []):
        raise ExperimentError("provider attempt receipt is superseded")
    status = attempt.get("status")
    if status == "succeeded":
        if receipt is None:
            raise ExperimentError("successful provider attempt requires a receipt")
        validate_provider_preflight(receipt, policy)
        success_expected = {
            "attempt_id": receipt.get("attempt_id"),
            "model_invocation_count": 1,
            "diagnostic": None,
            "receipt_digest": receipt.get("receipt_digest"),
        }
        for field, value in success_expected.items():
            if attempt.get(field) != value or type(attempt.get(field)) is not type(value):
                raise ExperimentError(f"provider attempt receipt {field} mismatch")
        timestamp(receipt.get("checked_at"), "provider preflight checked_at")
        checked = datetime.fromisoformat(
            str(receipt["checked_at"]).replace("Z", "+00:00")
        )
        if not started <= checked <= completed:
            raise ExperimentError("provider attempt receipt timeline mismatch")
    elif status == "failed":
        if receipt is not None:
            raise ExperimentError("failed provider attempt must not bind a receipt")
        if attempt.get("model_invocation_count") not in {0, 1}:
            raise ExperimentError("provider attempt receipt invocation count mismatch")
        _string(attempt.get("diagnostic"), "attempt diagnostic")
        if attempt.get("receipt_digest") is not None:
            raise ExperimentError("failed provider attempt receipt digest must be null")
    else:
        raise ExperimentError("provider attempt receipt is not terminal")
    stored_digest = require_sha256(
        attempt.get("attempt_digest"), "provider attempt attempt_digest"
    )
    without_digest = {
        key: value for key, value in attempt.items() if key != "attempt_digest"
    }
    if stored_digest != sha256_json(without_digest):
        raise ExperimentError("provider attempt receipt digest mismatch")
    return copy.deepcopy(dict(attempt))
