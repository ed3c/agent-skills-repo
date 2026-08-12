"""Pinned BenchFlow adapter for one-attempt paired Arena experiments.

The adapter consumes an already validated, content-addressed SkillsBench bundle.
It never asks BenchFlow to construct a multi-arm evaluation: the Arena plan owns
arm order and denominator identity.  Each invocation gets one native BenchFlow
YAML config with ``max_retries: 0`` and one ``bench eval run`` process.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Callable, Mapping, Sequence, cast

import yaml

from .model import (
    METRICS_SCHEMA,
    NO_SKILL,
    TRAJECTORY_SCHEMA,
    VERIFIER_SCHEMA,
    ExperimentError,
    InvocationCapture,
    canonical_bytes,
    identifier,
    regular_file,
    require_sha256,
    sha256_bytes,
    sha256_json,
)

POLICY_SCHEMA = "arena-benchflow-runtime-policy@1"
CATALOG_EVIDENCE_SCHEMA = "github-models-catalog-evidence@1"
PREPARATION_SCHEMA = "arena-benchflow-preparation@1"
PAIR_SUMMARY_SCHEMA = "arena-paired-result@1"
BENCHFLOW_CONFIG_SCHEMA = "arena-benchflow-invocation-config@1"

_POLICY_FIELDS = {
    "schema_version",
    "policy_id",
    "benchflow_version",
    "agent",
    "model",
    "catalog_model_id",
    "sandbox",
    "sandbox_profile_id",
    "task_id",
    "task_family",
    "task_bundle_digest",
    "candidate_skill_name",
    "usage_tracking",
    "max_retries",
    "concurrency",
    "skill_nudge",
    "network_policy",
    "allowed_tools",
    "sandbox_setup_timeout_sec",
    "agent_timeout_sec",
    "agent_idle_timeout_sec",
    "process_timeout_sec",
    "repetitions",
    "catalog_api_version",
    "catalog_url",
}
_CATALOG_FIELDS = {
    "schema_version",
    "catalog_url",
    "api_version",
    "model_id",
    "name",
    "publisher",
    "registry",
    "version",
    "capabilities",
    "limits",
    "rate_limit_tier",
    "fetched_at",
    "evidence_digest",
}
_PREPARATION_FIELDS = {
    "schema_version",
    "task_id",
    "task_bundle_digest",
    "candidate_skill_name",
    "candidate_skill_artifact_digest",
    "environment_image_digest",
    "docker_server_version",
    "runtime_policy_digest",
    "model_catalog_evidence_digest",
    "effective_policy_digest",
    "allowed_tools_digest",
    "preparation_digest",
}
_RETIREMENT_AUTHORITY_FIELDS = {
    "schema_version",
    "provider_id",
    "catalog_url",
    "model_prefix",
    "retired_on",
    "authority_url",
    "historical_run_handling",
}

_SECRET_KEY_RE = re.compile(
    r"(?:token|secret|password|passwd|authorization|credential|cookie|api[_-]?key)",
    re.IGNORECASE,
)
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_GITHUB_TOKEN_RE = re.compile(r"\bgh[opsu]_[A-Za-z0-9_]{20,}\b")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9._:/+\-]{1,128}$")

# BenchFlow 0.6.3 stable categories, intentionally copied as data rather than
# importing BenchFlow internals into the Arena control plane.
_TIMEOUT_CATEGORIES = {"timeout", "idle_timeout"}
_TRANSPORT_CATEGORIES = {"pipe_closed", "acp_error"}
_INFRA_CATEGORIES = {
    "install_failure",
    "infra_failure",
    "sandbox_setup",
    "provider_auth",
    "provider_rate_limit",
    "api_error",
    "suspected_api_error",
}
_VERIFIER_TIMEOUT_CATEGORIES = {"verifier_timeout"}
_VERIFIER_INFRA_CATEGORIES = {
    "verifier_failure",
    "verifier_infra",
    "verifier_dep_install",
    "verifier_other",
}

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
UrlOpener = Callable[..., object]


def _run_text(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=cwd,
        env=None if env is None else dict(env),
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


def _exact_fields(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ExperimentError(
            f"{label} fields differ: expected={sorted(expected)} actual={sorted(value)}"
        )


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ExperimentError(f"{label} must be a non-empty string")
    return value


def _positive_int(value: object, label: str, *, minimum: int = 1) -> int:
    if type(value) is not int or cast(int, value) < minimum:
        raise ExperimentError(f"{label} must be an integer >= {minimum}")
    return cast(int, value)


def _canonical_digest_without(value: Mapping[str, object], field: str) -> str:
    return sha256_json({key: item for key, item in value.items() if key != field})


@dataclass(frozen=True)
class BenchFlowRuntimePolicy:
    policy_id: str
    benchflow_version: str
    agent: str
    model: str
    catalog_model_id: str
    sandbox: str
    sandbox_profile_id: str
    task_id: str
    task_family: str
    task_bundle_digest: str
    candidate_skill_name: str
    usage_tracking: str
    max_retries: int
    concurrency: int
    skill_nudge: str
    network_policy: str
    allowed_tools: tuple[str, ...]
    sandbox_setup_timeout_sec: int
    agent_timeout_sec: int
    agent_idle_timeout_sec: int
    process_timeout_sec: int
    repetitions: int
    catalog_api_version: str
    catalog_url: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "BenchFlowRuntimePolicy":
        _exact_fields(raw, _POLICY_FIELDS, "BenchFlow runtime policy")
        if raw.get("schema_version") != POLICY_SCHEMA:
            raise ExperimentError("BenchFlow runtime policy schema is unsupported")
        allowed_raw = raw.get("allowed_tools")
        if not isinstance(allowed_raw, list) or not allowed_raw:
            raise ExperimentError("BenchFlow runtime policy allowed_tools is empty")
        allowed: list[str] = []
        for item in allowed_raw:
            name = _nonempty_string(item, "allowed tool")
            if _TOOL_NAME_RE.fullmatch(name) is None:
                raise ExperimentError(f"allowed tool name is invalid: {name!r}")
            allowed.append(name)
        if allowed != sorted(allowed) or len(allowed) != len(set(allowed)):
            raise ExperimentError("allowed_tools must be sorted and unique")
        policy = cls(
            policy_id=identifier(raw.get("policy_id"), "policy_id"),
            benchflow_version=_nonempty_string(
                raw.get("benchflow_version"), "benchflow_version"
            ),
            agent=identifier(raw.get("agent"), "agent"),
            model=_nonempty_string(raw.get("model"), "model"),
            catalog_model_id=_nonempty_string(
                raw.get("catalog_model_id"), "catalog_model_id"
            ),
            sandbox=_nonempty_string(raw.get("sandbox"), "sandbox"),
            sandbox_profile_id=identifier(
                raw.get("sandbox_profile_id"), "sandbox_profile_id"
            ),
            task_id=identifier(raw.get("task_id"), "task_id"),
            task_family=identifier(raw.get("task_family"), "task_family"),
            task_bundle_digest=require_sha256(
                raw.get("task_bundle_digest"), "task_bundle_digest"
            ),
            candidate_skill_name=identifier(
                raw.get("candidate_skill_name"), "candidate_skill_name"
            ),
            usage_tracking=_nonempty_string(
                raw.get("usage_tracking"), "usage_tracking"
            ),
            max_retries=_positive_int(
                raw.get("max_retries"), "max_retries", minimum=0
            ),
            concurrency=_positive_int(raw.get("concurrency"), "concurrency"),
            skill_nudge=_nonempty_string(raw.get("skill_nudge"), "skill_nudge"),
            network_policy=_nonempty_string(
                raw.get("network_policy"), "network_policy"
            ),
            allowed_tools=tuple(allowed),
            sandbox_setup_timeout_sec=_positive_int(
                raw.get("sandbox_setup_timeout_sec"), "sandbox_setup_timeout_sec"
            ),
            agent_timeout_sec=_positive_int(
                raw.get("agent_timeout_sec"), "agent_timeout_sec"
            ),
            agent_idle_timeout_sec=_positive_int(
                raw.get("agent_idle_timeout_sec"), "agent_idle_timeout_sec"
            ),
            process_timeout_sec=_positive_int(
                raw.get("process_timeout_sec"), "process_timeout_sec"
            ),
            repetitions=_positive_int(raw.get("repetitions"), "repetitions"),
            catalog_api_version=_nonempty_string(
                raw.get("catalog_api_version"), "catalog_api_version"
            ),
            catalog_url=_nonempty_string(raw.get("catalog_url"), "catalog_url"),
        )
        if policy.benchflow_version != "0.6.3":
            raise ExperimentError("the first Arena adapter pins BenchFlow 0.6.3")
        if policy.agent != "pi-acp":
            raise ExperimentError("the first real Arena profile pins pi-acp")
        if policy.model != f"github-models/{policy.catalog_model_id}":
            raise ExperimentError("model must be bound to the github-models provider")
        if policy.sandbox != "docker":
            raise ExperimentError("the first real Arena profile pins Docker")
        if policy.usage_tracking != "required":
            raise ExperimentError("usage tracking must be required")
        if policy.max_retries != 0:
            raise ExperimentError("BenchFlow internal retries must be disabled")
        if policy.concurrency != 1:
            raise ExperimentError("one invocation may execute only one task")
        if policy.skill_nudge != "name":
            raise ExperimentError("the first task-local skill profile pins name nudge")
        if policy.network_policy not in {"public", "no-network"}:
            raise ExperimentError("network_policy is unsupported")
        if policy.repetitions < 3:
            raise ExperimentError("Arena MVP requires at least three repetitions")
        if policy.process_timeout_sec <= policy.agent_timeout_sec:
            raise ExperimentError("process timeout must exceed agent timeout")
        return policy

    def as_mapping(self) -> dict[str, object]:
        return {
            "schema_version": POLICY_SCHEMA,
            "policy_id": self.policy_id,
            "benchflow_version": self.benchflow_version,
            "agent": self.agent,
            "model": self.model,
            "catalog_model_id": self.catalog_model_id,
            "sandbox": self.sandbox,
            "sandbox_profile_id": self.sandbox_profile_id,
            "task_id": self.task_id,
            "task_family": self.task_family,
            "task_bundle_digest": self.task_bundle_digest,
            "candidate_skill_name": self.candidate_skill_name,
            "usage_tracking": self.usage_tracking,
            "max_retries": self.max_retries,
            "concurrency": self.concurrency,
            "skill_nudge": self.skill_nudge,
            "network_policy": self.network_policy,
            "allowed_tools": list(self.allowed_tools),
            "sandbox_setup_timeout_sec": self.sandbox_setup_timeout_sec,
            "agent_timeout_sec": self.agent_timeout_sec,
            "agent_idle_timeout_sec": self.agent_idle_timeout_sec,
            "process_timeout_sec": self.process_timeout_sec,
            "repetitions": self.repetitions,
            "catalog_api_version": self.catalog_api_version,
            "catalog_url": self.catalog_url,
        }

    @property
    def digest(self) -> str:
        return sha256_json(self.as_mapping())

    @property
    def allowed_tools_digest(self) -> str:
        return sha256_json({"allowed_tools": list(self.allowed_tools)})


def load_runtime_policy(path: Path | str) -> BenchFlowRuntimePolicy:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read BenchFlow runtime policy: {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExperimentError("BenchFlow runtime policy root must be an object")
    return BenchFlowRuntimePolicy.from_mapping(value)


def fetch_github_model_catalog_evidence(
    *,
    token: str,
    policy: BenchFlowRuntimePolicy,
    fetched_at: datetime,
    retirement_authority: Mapping[str, object] | None = None,
    opener: UrlOpener = urllib.request.urlopen,
) -> dict[str, object]:
    if retirement_authority is None:
        raise ExperimentError("GitHub Models retirement authority is required")
    _exact_fields(
        retirement_authority,
        _RETIREMENT_AUTHORITY_FIELDS,
        "GitHub Models retirement authority",
    )
    exact = {
        "schema_version": "github-models-retirement-authority@1",
        "provider_id": "github-models",
        "catalog_url": policy.catalog_url,
        "model_prefix": "github-models/",
        "retired_on": "2026-07-30",
        "authority_url": (
            "https://github.blog/changelog/2026-07-01-"
            "github-models-is-being-fully-retired-on-july-30-2026/"
        ),
        "historical_run_handling": "preserve-no-rejudging",
    }
    if any(retirement_authority.get(key) != value for key, value in exact.items()):
        raise ExperimentError(
            "GitHub Models retirement authority does not match the runtime policy"
        )
    retired_on_raw = _nonempty_string(
        retirement_authority.get("retired_on"), "retirement retired_on"
    )
    authority_url = _nonempty_string(
        retirement_authority.get("authority_url"), "retirement authority_url"
    )
    try:
        retired_on = date.fromisoformat(retired_on_raw)
    except ValueError as exc:
        raise ExperimentError("retirement retired_on must be an ISO date") from exc
    if fetched_at.tzinfo is None:
        raise ExperimentError("catalog fetched_at must be timezone-aware")
    if fetched_at.astimezone(timezone.utc).date() >= retired_on:
        raise ExperimentError(
            "provider_retired "
            f"provider=github-models retired_on={retired_on_raw} "
            f"authority={authority_url}"
        )
    if not token:
        raise ExperimentError("GitHub Models token is absent")
    request = urllib.request.Request(
        policy.catalog_url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": policy.catalog_api_version,
            "User-Agent": "agent-skills-repo-arena/1",
        },
    )
    try:
        response = opener(request, timeout=30)
        raw = response.read()  # type: ignore[attr-defined]
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ExperimentError(
                "provider_catalog_authorization_failed "
                f"status={exc.code} catalog_url={policy.catalog_url}"
            ) from exc
        raise ExperimentError(
            "provider_catalog_http_error "
            f"status={exc.code} catalog_url={policy.catalog_url}"
        ) from exc
    except (OSError, urllib.error.URLError) as exc:
        raise ExperimentError(
            "provider_catalog_transport_error "
            f"catalog_url={policy.catalog_url}"
        ) from exc
    try:
        catalog = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError("GitHub Models catalog is not valid UTF-8 JSON") from exc
    if not isinstance(catalog, list):
        raise ExperimentError("provider_catalog_schema_error root_must_be_array")
    matches = [item for item in catalog if isinstance(item, dict) and item.get("id") == policy.catalog_model_id]
    if len(matches) != 1:
        raise ExperimentError(
            f"GitHub Models catalog must contain exactly one {policy.catalog_model_id!r}"
        )
    model = matches[0]
    capabilities = model.get("capabilities")
    limits = model.get("limits")
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise ExperimentError("catalog model capabilities are invalid")
    if not isinstance(limits, dict):
        raise ExperimentError("catalog model limits are invalid")
    evidence_without_digest: dict[str, object] = {
        "schema_version": CATALOG_EVIDENCE_SCHEMA,
        "catalog_url": policy.catalog_url,
        "api_version": policy.catalog_api_version,
        "model_id": policy.catalog_model_id,
        "name": _nonempty_string(model.get("name"), "catalog model name"),
        "publisher": _nonempty_string(model.get("publisher"), "catalog model publisher"),
        "registry": _nonempty_string(model.get("registry"), "catalog model registry"),
        "version": _nonempty_string(model.get("version"), "catalog model version"),
        "capabilities": sorted(capabilities),
        "limits": copy.deepcopy(limits),
        "rate_limit_tier": _nonempty_string(
            model.get("rate_limit_tier"), "catalog model rate_limit_tier"
        ),
        "fetched_at": fetched_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return {
        **evidence_without_digest,
        "evidence_digest": sha256_json(evidence_without_digest),
    }


def validate_catalog_evidence(
    evidence: Mapping[str, object], policy: BenchFlowRuntimePolicy
) -> dict[str, object]:
    _exact_fields(evidence, _CATALOG_FIELDS, "GitHub Models catalog evidence")
    if evidence.get("schema_version") != CATALOG_EVIDENCE_SCHEMA:
        raise ExperimentError("GitHub Models catalog evidence schema is unsupported")
    if evidence.get("catalog_url") != policy.catalog_url or evidence.get("api_version") != policy.catalog_api_version:
        raise ExperimentError("GitHub Models catalog evidence source differs from policy")
    if evidence.get("model_id") != policy.catalog_model_id:
        raise ExperimentError("GitHub Models catalog evidence is bound to another model")
    if evidence.get("evidence_digest") != _canonical_digest_without(evidence, "evidence_digest"):
        raise ExperimentError("GitHub Models catalog evidence digest mismatch")
    return copy.deepcopy(dict(evidence))


def _bundle_manifest(bundle_dir: Path, policy: BenchFlowRuntimePolicy) -> dict[str, object]:
    # Reuse the already-landed SkillsBench structural authority before applying
    # the narrower first-runtime-profile checks below.  Import lazily so the
    # experiment package stays harness-independent at module import time.
    try:
        from arena_adapters.skillsbench import validate_bundle_directory
    except ImportError as exc:  # pragma: no cover - repository wiring failure
        raise ExperimentError(
            "SkillsBench bundle validator is unavailable"
        ) from exc
    try:
        validate_bundle_directory(bundle_dir)
    except Exception as exc:  # fail closed across adapter exception types
        raise ExperimentError(
            f"SkillsBench bundle structural validation failed: {type(exc).__name__}"
        ) from exc

    bundle_path = bundle_dir / "bundle.json"
    try:
        value = json.loads(bundle_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read SkillsBench bundle manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise ExperimentError("SkillsBench bundle manifest root must be an object")
    if value.get("schema_version") != "arena-task-bundle@1":
        raise ExperimentError("SkillsBench bundle schema is unsupported")
    if value.get("bundle_digest") != policy.task_bundle_digest:
        raise ExperimentError("SkillsBench bundle digest differs from runtime policy")
    task = value.get("task")
    skills = value.get("skill_injection")
    if not isinstance(task, dict) or task.get("task_id") != policy.task_id:
        raise ExperimentError("SkillsBench bundle is bound to another task")
    if task.get("role") != policy.task_family:
        raise ExperimentError("SkillsBench task family differs from runtime policy")
    if task.get("network_mode") != policy.network_policy:
        raise ExperimentError("SkillsBench task network mode differs from runtime policy")
    if not isinstance(skills, dict) or skills.get("skill_names") != [policy.candidate_skill_name]:
        raise ExperimentError("the first paired task must expose exactly one pinned skill")
    package = bundle_dir / "package"
    if package.is_symlink() or not package.is_dir():
        raise ExperimentError("SkillsBench normalized package is absent or unsafe")
    return value


def _git_mode(path: Path) -> str:
    return "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"


def compute_skill_artifact_digest(skill_dir: Path | str) -> str:
    root = Path(skill_dir)
    if root.is_symlink() or not root.is_dir():
        raise ExperimentError(f"candidate skill directory is absent or unsafe: {root}")
    resolved = root.resolve(strict=True)
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ExperimentError(f"candidate skill contains symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ExperimentError(f"candidate skill contains special file: {relative}")
        try:
            path.resolve(strict=True).relative_to(resolved)
        except ValueError as exc:
            raise ExperimentError(f"candidate skill file escapes root: {relative}") from exc
        data = regular_file(path, f"candidate skill file {relative}")
        entries.append(
            {
                "path": relative,
                "sha256": sha256_bytes(data),
                "size_bytes": len(data),
                "git_mode": _git_mode(path),
            }
        )
    if not entries or not any(item["path"] == "SKILL.md" for item in entries):
        raise ExperimentError("candidate skill must contain SKILL.md")
    return sha256_json({"schema_version": "arena-skill-artifact@1", "files": entries})


def _docker_image_identity(
    package: Path,
    *,
    tag: str,
    runner: CommandRunner,
) -> tuple[str, str]:
    environment = package / "environment"
    if environment.is_symlink() or not (environment / "Dockerfile").is_file():
        raise ExperimentError("task environment Dockerfile is absent or unsafe")
    build = runner(
        ["docker", "build", "--quiet", "--tag", tag, str(environment)],
        cwd=package,
        timeout=1200,
    )
    if build.returncode != 0:
        raise ExperimentError("task environment image build failed")
    inspect = runner(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        timeout=60,
    )
    if inspect.returncode != 0:
        raise ExperimentError("cannot inspect task environment image")
    image = inspect.stdout.strip()
    require_sha256(image, "task environment image identity")
    version = runner(
        ["docker", "version", "--format", "{{.Server.Version}}"], timeout=60
    )
    if version.returncode != 0 or not version.stdout.strip():
        raise ExperimentError("cannot read Docker server version")
    return image, version.stdout.strip()


def prepare_benchflow_runtime(
    *,
    bundle_dir: Path | str,
    policy: BenchFlowRuntimePolicy,
    catalog_evidence: Mapping[str, object],
    image_tag: str,
    runner: CommandRunner = _run_text,
) -> dict[str, object]:
    bundle_root = Path(bundle_dir)
    _bundle_manifest(bundle_root, policy)
    validated_catalog = validate_catalog_evidence(catalog_evidence, policy)
    skill_dir = (
        bundle_root
        / "package"
        / "environment"
        / "skills"
        / policy.candidate_skill_name
    )
    skill_digest = compute_skill_artifact_digest(skill_dir)
    image_digest, docker_version = _docker_image_identity(
        bundle_root / "package", tag=image_tag, runner=runner
    )
    effective_policy_digest = sha256_json(
        {
            "runtime_policy_digest": policy.digest,
            "model_catalog_evidence_digest": validated_catalog["evidence_digest"],
        }
    )
    preparation_without_digest: dict[str, object] = {
        "schema_version": PREPARATION_SCHEMA,
        "task_id": policy.task_id,
        "task_bundle_digest": policy.task_bundle_digest,
        "candidate_skill_name": policy.candidate_skill_name,
        "candidate_skill_artifact_digest": skill_digest,
        "environment_image_digest": image_digest,
        "docker_server_version": docker_version,
        "runtime_policy_digest": policy.digest,
        "model_catalog_evidence_digest": validated_catalog["evidence_digest"],
        "effective_policy_digest": effective_policy_digest,
        "allowed_tools_digest": policy.allowed_tools_digest,
    }
    return {
        **preparation_without_digest,
        "preparation_digest": sha256_json(preparation_without_digest),
    }


def validate_preparation(
    preparation: Mapping[str, object],
    policy: BenchFlowRuntimePolicy,
    catalog_evidence: Mapping[str, object],
) -> dict[str, object]:
    _exact_fields(preparation, _PREPARATION_FIELDS, "BenchFlow preparation")
    if preparation.get("schema_version") != PREPARATION_SCHEMA:
        raise ExperimentError("BenchFlow preparation schema is unsupported")
    expected = {
        "task_id": policy.task_id,
        "task_bundle_digest": policy.task_bundle_digest,
        "candidate_skill_name": policy.candidate_skill_name,
        "runtime_policy_digest": policy.digest,
        "model_catalog_evidence_digest": validate_catalog_evidence(
            catalog_evidence, policy
        )["evidence_digest"],
        "allowed_tools_digest": policy.allowed_tools_digest,
    }
    if any(preparation.get(key) != value for key, value in expected.items()):
        raise ExperimentError("BenchFlow preparation binding mismatch")
    for field in (
        "candidate_skill_artifact_digest",
        "environment_image_digest",
        "effective_policy_digest",
        "preparation_digest",
    ):
        require_sha256(preparation.get(field), f"preparation {field}")
    if preparation.get("preparation_digest") != _canonical_digest_without(
        preparation, "preparation_digest"
    ):
        raise ExperimentError("BenchFlow preparation digest mismatch")
    return copy.deepcopy(dict(preparation))


def _scrub_text(text: str, secrets: Sequence[str]) -> str:
    changed = text
    for secret in secrets:
        if secret:
            changed = changed.replace(secret, "[REDACTED]")
    changed = _BEARER_RE.sub("Bearer [REDACTED]", changed)
    changed = _GITHUB_TOKEN_RE.sub("[REDACTED_GITHUB_TOKEN]", changed)
    return changed


def _scrub_json(value: object, secrets: Sequence[str]) -> object:
    if isinstance(value, dict):
        result: dict[str, object] = {}
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                result[str(key)] = "[REDACTED]"
            else:
                result[str(key)] = _scrub_json(item, secrets)
        return result
    if isinstance(value, list):
        return [_scrub_json(item, secrets) for item in value]
    if isinstance(value, str):
        return _scrub_text(value, secrets)
    return value


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExperimentError(f"{label} root must be an object")
    return value


def _unique_file(root: Path, name: str, label: str) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise ExperimentError(f"{label} root is absent or unsafe")
    matches = sorted(root.rglob(name))
    if len(matches) != 1:
        raise ExperimentError(f"{label} must contain exactly one {name}: {len(matches)}")
    regular_file(matches[0], f"{label} {name}")
    return matches[0]


def _numeric(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExperimentError(f"{label} must be numeric")
    return float(value)


def _reward(result: Mapping[str, object]) -> float | None:
    rewards = result.get("rewards")
    if rewards is None:
        return None
    if not isinstance(rewards, dict):
        raise ExperimentError("BenchFlow result rewards must be an object")
    value = rewards.get("reward")
    if value is None:
        return None
    return _numeric(value, "BenchFlow reward")


def _time_metrics(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    text = path.read_text(encoding="utf-8", errors="replace")
    user = system = 0.0
    rss_kib = 0
    for line in text.splitlines():
        key, _, raw = line.partition(":")
        value = raw.strip()
        if key.strip() == "User time (seconds)":
            try:
                user = float(value)
            except ValueError:
                pass
        elif key.strip() == "System time (seconds)":
            try:
                system = float(value)
            except ValueError:
                pass
        elif key.strip() == "Maximum resident set size (kbytes)":
            try:
                rss_kib = int(value)
            except ValueError:
                pass
    return int(round((user + system) * 1000)), rss_kib * 1024


def _cost_microunits(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ExperimentError("BenchFlow cost_usd is invalid")
    try:
        return int(
            (Decimal(str(value)) * Decimal("1000000")).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    except (InvalidOperation, ValueError) as exc:
        raise ExperimentError("BenchFlow cost_usd is invalid") from exc


def _trajectory_events(rollout_root: Path, secrets: Sequence[str]) -> tuple[list[dict[str, object]], bytes]:
    candidates = [
        rollout_root / "trajectory" / "acp_trajectory.jsonl",
        rollout_root / "agent" / "acp_trajectory.jsonl",
    ]
    path = next((item for item in candidates if item.is_file() and not item.is_symlink()), None)
    if path is None:
        raise ExperimentError("BenchFlow structured trajectory is absent")
    raw = regular_file(path, "BenchFlow trajectory")
    events: list[dict[str, object]] = []
    safe_lines: list[bytes] = []
    for index, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        try:
            item = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExperimentError(f"BenchFlow trajectory line {index} is invalid") from exc
        if not isinstance(item, dict):
            raise ExperimentError(f"BenchFlow trajectory line {index} is not an object")
        safe = cast(dict[str, object], _scrub_json(item, secrets))
        events.append(safe)
        safe_lines.append(canonical_bytes(safe))
    if not events:
        raise ExperimentError("BenchFlow structured trajectory has no events")
    return events, b"\n".join(safe_lines) + b"\n"


def _tool_names(events: Sequence[Mapping[str, object]]) -> list[str]:
    names: set[str] = set()
    stack: list[object] = list(events)
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            kind = value.get("type") or value.get("kind")
            if kind in {"tool_call", "tool", "tool_use"}:
                candidate = value.get("name") or value.get("tool_name") or value.get("title")
                if isinstance(candidate, str) and candidate:
                    names.add(candidate)
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return sorted(names)


def _classification(result: Mapping[str, object], reward: float | None) -> tuple[str, str | None]:
    verifier_error = result.get("verifier_error")
    verifier_category = result.get("verifier_error_category")
    error = result.get("error")
    error_category = result.get("error_category")
    if verifier_error is not None:
        category = verifier_category if isinstance(verifier_category, str) else "verifier_error"
        if category in _VERIFIER_TIMEOUT_CATEGORIES:
            return "timeout", category
        if category in _VERIFIER_INFRA_CATEGORIES:
            return "verifier_failure", category
        return "verifier_failure", category
    if error is not None:
        category = error_category if isinstance(error_category, str) else "agent_error"
        if category == "agent_refusal":
            return "agent_refusal", category
        if category in _TIMEOUT_CATEGORIES:
            return "timeout", category
        if category in _TRANSPORT_CATEGORIES:
            return "transport_loss", category
        if category in _INFRA_CATEGORIES:
            return "infrastructure_failure", category
        return "infrastructure_failure", category
    if reward is None:
        return "infrastructure_failure", "missing_reward"
    if reward == 1.0:
        return "succeeded", None
    return "task_failure", "verifier_reward_below_one"


def _safe_environment(base: Mapping[str, str], required_token: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in base.items():
        if _SECRET_KEY_RE.search(key) and key != "GITHUB_TOKEN":
            continue
        env[key] = value
    env["GITHUB_TOKEN"] = required_token
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _write_yaml(path: Path, value: Mapping[str, object]) -> bytes:
    raw = yaml.safe_dump(dict(value), sort_keys=True).encode("utf-8")
    path.write_bytes(raw)
    return raw


class BenchFlowExperimentAdapter:
    """Execute one preregistered invocation through BenchFlow 0.6.3."""

    def __init__(
        self,
        *,
        bench_bin: Path | str,
        bundle_dir: Path | str,
        policy: BenchFlowRuntimePolicy,
        catalog_evidence: Mapping[str, object],
        preparation: Mapping[str, object],
        github_token: str,
        image_tag_prefix: str,
        runner: CommandRunner = _run_text,
        base_environment: Mapping[str, str] | None = None,
    ) -> None:
        self.bench_bin = Path(bench_bin)
        if not self.bench_bin.is_file():
            raise ExperimentError(f"BenchFlow executable is absent: {self.bench_bin}")
        self.bundle_dir = Path(bundle_dir)
        self.policy = policy
        self.catalog_evidence = validate_catalog_evidence(catalog_evidence, policy)
        self.preparation = validate_preparation(preparation, policy, catalog_evidence)
        self.github_token = _nonempty_string(github_token, "GitHub Models token")
        self.image_tag_prefix = identifier(image_tag_prefix, "image_tag_prefix")
        self.runner = runner
        self.base_environment = dict(base_environment or os.environ)
        _bundle_manifest(self.bundle_dir, policy)

    def _validate_invocation(self, invocation: Mapping[str, object]) -> str:
        expected = {
            "task_id": self.policy.task_id,
            "task_family": self.policy.task_family,
            "task_bundle_digest": self.policy.task_bundle_digest,
            "agent_id": self.policy.agent,
            "model_id": self.policy.model,
            "harness_id": "benchflow",
            "harness_version": self.policy.benchflow_version,
            "sandbox_profile_id": self.policy.sandbox_profile_id,
            "environment_image_digest": self.preparation["environment_image_digest"],
            "policy_digest": self.preparation["effective_policy_digest"],
            "network_policy": self.policy.network_policy,
            "allowed_tools_digest": self.policy.allowed_tools_digest,
        }
        if any(invocation.get(key) != value for key, value in expected.items()):
            raise ExperimentError("Arena invocation differs from prepared BenchFlow envelope")
        arm = invocation.get("arm")
        if arm not in {"baseline", "candidate"}:
            raise ExperimentError("the first BenchFlow adapter supports baseline/candidate only")
        expected_skill = (
            NO_SKILL
            if arm == "baseline"
            else self.preparation["candidate_skill_artifact_digest"]
        )
        if invocation.get("skill_artifact_digest") != expected_skill:
            raise ExperimentError("Arena invocation skill identity mismatch")
        if invocation.get("agent_seed") is not None:
            raise ExperimentError("GitHub Models profile does not claim deterministic seeding")
        return cast(str, arm)

    def _verify_image(self, invocation_id: str) -> None:
        image, _ = _docker_image_identity(
            self.bundle_dir / "package",
            tag=f"{self.image_tag_prefix}:{invocation_id}",
            runner=self.runner,
        )
        if image != self.preparation["environment_image_digest"]:
            raise ExperimentError("task environment image drifted after preregistration")

    def execute(
        self,
        invocation: Mapping[str, object],
        workspace: Path,
    ) -> InvocationCapture:
        arm = self._validate_invocation(invocation)
        invocation_id = identifier(invocation.get("invocation_id"), "invocation_id")
        self._verify_image(invocation_id)
        package = self.bundle_dir / "package"
        jobs = workspace / "jobs"
        config_path = workspace / "benchflow.yaml"
        time_path = workspace / "resource-time.txt"
        skill_mode = "no-skill" if arm == "baseline" else "with-skill"
        agent_env: dict[str, str] = {}
        if arm == "candidate":
            agent_env["BENCHFLOW_SKILL_NUDGE"] = self.policy.skill_nudge
        config: dict[str, object] = {
            "schema_version": BENCHFLOW_CONFIG_SCHEMA,
            "tasks_dir": str(package),
            "jobs_dir": str(jobs),
            "agent": self.policy.agent,
            "model": self.policy.model,
            "environment": self.policy.sandbox,
            "concurrency": self.policy.concurrency,
            "max_retries": self.policy.max_retries,
            "skill_mode": skill_mode,
            "usage_tracking": self.policy.usage_tracking,
            "sandbox_setup_timeout": self.policy.sandbox_setup_timeout_sec,
            "agent_timeout": self.policy.agent_timeout_sec,
            "agent_idle_timeout": self.policy.agent_idle_timeout_sec,
            "agent_env": agent_env,
        }
        # BenchFlow does not know the Arena-only schema marker.
        benchflow_config = {key: value for key, value in config.items() if key != "schema_version"}
        config_bytes = _write_yaml(config_path, benchflow_config)
        secrets = [self.github_token]
        started_at = datetime.now(timezone.utc)
        command = [
            "/usr/bin/time",
            "-v",
            "-o",
            str(time_path),
            str(self.bench_bin),
            "eval",
            "run",
            "--config",
            str(config_path),
        ]
        try:
            completed = self.runner(
                command,
                cwd=workspace,
                env=_safe_environment(self.base_environment, self.github_token),
                timeout=self.policy.process_timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            finished_at = datetime.now(timezone.utc)
            stdout = _scrub_text(str(exc.stdout or ""), secrets).encode("utf-8")
            stderr = _scrub_text(str(exc.stderr or ""), secrets).encode("utf-8")
            return InvocationCapture(
                classification="timeout",
                reward=None,
                adapter_exit_code=None,
                error_code="benchflow_process_timeout",
                stdout=stdout,
                stderr=stderr,
                trajectory={"schema_version": TRAJECTORY_SCHEMA, "events": []},
                verifier={
                    "schema_version": VERIFIER_SCHEMA,
                    "status": "not_run_process_timeout",
                    "reward": None,
                    "diagnostics_digest": None,
                },
                metrics={
                    "schema_version": METRICS_SCHEMA,
                    "end_to_end_latency_ms": int((finished_at - started_at).total_seconds() * 1000),
                    "verifier_latency_ms": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "tool_tokens": 0,
                    "cost_microunits": 0,
                    "cpu_time_ms": 0,
                    "peak_memory_bytes": 0,
                    "tool_call_count": 0,
                },
                artifacts={
                    "benchflow-config.yaml": config_bytes,
                    "runtime-preparation.json": canonical_bytes(self.preparation),
                    "model-catalog-evidence.json": canonical_bytes(self.catalog_evidence),
                },
                started_at=started_at,
                completed_at=finished_at,
            )

        finished_at = datetime.now(timezone.utc)
        stdout_text = _scrub_text(completed.stdout, secrets)
        stderr_text = _scrub_text(completed.stderr, secrets)
        artifacts: dict[str, bytes] = {
            "benchflow-config.yaml": config_bytes,
            "benchflow-stdout.txt": stdout_text.encode("utf-8"),
            "benchflow-stderr.txt": stderr_text.encode("utf-8"),
            "runtime-preparation.json": canonical_bytes(self.preparation),
            "model-catalog-evidence.json": canonical_bytes(self.catalog_evidence),
        }
        if time_path.is_file():
            artifacts["resource-time.txt"] = regular_file(time_path, "resource time evidence")

        try:
            result_path = _unique_file(jobs, "result.json", "BenchFlow jobs")
        except ExperimentError:
            cpu_ms, memory = _time_metrics(time_path)
            return InvocationCapture(
                classification="infrastructure_failure",
                reward=None,
                adapter_exit_code=completed.returncode,
                error_code="benchflow_result_absent",
                stdout=stdout_text.encode("utf-8"),
                stderr=stderr_text.encode("utf-8"),
                trajectory={"schema_version": TRAJECTORY_SCHEMA, "events": []},
                verifier={
                    "schema_version": VERIFIER_SCHEMA,
                    "status": "not_run_result_absent",
                    "reward": None,
                    "diagnostics_digest": None,
                },
                metrics={
                    "schema_version": METRICS_SCHEMA,
                    "end_to_end_latency_ms": int((finished_at - started_at).total_seconds() * 1000),
                    "verifier_latency_ms": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "tool_tokens": 0,
                    "cost_microunits": 0,
                    "cpu_time_ms": cpu_ms,
                    "peak_memory_bytes": memory,
                    "tool_call_count": 0,
                },
                artifacts=artifacts,
                started_at=started_at,
                completed_at=finished_at,
            )

        rollout = result_path.parent
        result = _read_json(result_path, "BenchFlow result")
        safe_result = cast(dict[str, object], _scrub_json(result, secrets))
        artifacts["benchflow-result.json"] = canonical_bytes(safe_result)
        if (rollout / "timing.json").is_file():
            artifacts["benchflow-timing.json"] = regular_file(
                rollout / "timing.json", "BenchFlow timing"
            )
        events, safe_trajectory = _trajectory_events(rollout, secrets)
        artifacts["benchflow-trajectory.jsonl"] = safe_trajectory

        if result.get("agent") != self.policy.agent:
            raise ExperimentError("BenchFlow result agent differs from runtime policy")
        if result.get("model") != self.policy.model:
            raise ExperimentError("BenchFlow result model differs from runtime policy")
        if result.get("skill_mode") != skill_mode:
            raise ExperimentError("BenchFlow result skill mode differs from Arena arm")
        if arm == "baseline":
            if result.get("include_task_skills") is not False or result.get("effective_skills_dir") is not None:
                raise ExperimentError("baseline was exposed to task-local skills")
        else:
            if result.get("include_task_skills") is not True or not isinstance(result.get("effective_skills_dir"), str):
                raise ExperimentError("candidate task-local skill exposure is not proven")
        task_digest = require_sha256(result.get("task_digest"), "BenchFlow task digest")
        usage = result.get("usage_tracking")
        if not isinstance(usage, dict) or usage.get("requested") != self.policy.usage_tracking:
            raise ExperimentError("BenchFlow usage tracking request differs from policy")
        agent_result = result.get("agent_result")
        if not isinstance(agent_result, dict):
            raise ExperimentError("BenchFlow agent_result is absent")

        reward = _reward(result)
        classification, error_code = _classification(result, reward)
        tool_names = _tool_names(events)
        unknown_tools = sorted(set(tool_names) - set(self.policy.allowed_tools))
        if unknown_tools:
            classification, error_code, reward = (
                "infrastructure_failure",
                "unapproved_tool_call:" + ",".join(unknown_tools),
                None,
            )
        if classification not in {"succeeded", "task_failure"}:
            reward = None

        verifier_dir = rollout / "verifier"
        verifier_files: dict[str, bytes] = {}
        if verifier_dir.is_dir() and not verifier_dir.is_symlink():
            for name in ("reward.txt", "ctrf.json", "pytest_output.txt", "test-stdout.txt"):
                path = verifier_dir / name
                if path.is_file() and not path.is_symlink():
                    data = regular_file(path, f"BenchFlow verifier {name}")
                    verifier_files[name] = data
                    artifacts[f"verifier/{name}"] = data
        diagnostics_digest = (
            sha256_json(
                {
                    "task_digest": task_digest,
                    "reward": reward,
                    "error_category": result.get("error_category"),
                    "verifier_error_category": result.get("verifier_error_category"),
                    "verifier_files": {
                        key: sha256_bytes(value)
                        for key, value in sorted(verifier_files.items())
                    },
                }
            )
            if verifier_files or reward is not None
            else None
        )
        verifier_status = (
            "passed"
            if classification == "succeeded"
            else "failed"
            if classification == "task_failure"
            else "verifier_failure"
            if classification == "verifier_failure"
            else "not_scored"
        )

        timing = result.get("timing")
        timing = timing if isinstance(timing, dict) else {}
        total_seconds = timing.get("total")
        verifier_seconds = timing.get("verifier")
        end_to_end_ms = (
            int(round(_numeric(total_seconds, "BenchFlow total timing") * 1000))
            if total_seconds is not None
            else int((finished_at - started_at).total_seconds() * 1000)
        )
        verifier_ms = (
            int(round(_numeric(verifier_seconds, "BenchFlow verifier timing") * 1000))
            if verifier_seconds is not None
            else 0
        )
        cpu_ms, memory = _time_metrics(time_path)
        input_tokens = agent_result.get("n_input_tokens")
        output_tokens = agent_result.get("n_output_tokens")
        cache_read = agent_result.get("n_cache_read_tokens")
        cache_create = agent_result.get("n_cache_creation_tokens")
        token_values = [input_tokens, output_tokens, cache_read, cache_create]
        if any(value is not None and (type(value) is not int or cast(int, value) < 0) for value in token_values):
            raise ExperimentError("BenchFlow token telemetry is invalid")
        if self.policy.usage_tracking == "required" and classification in {"succeeded", "task_failure"}:
            if usage.get("usage_source") == "unavailable" or input_tokens is None or output_tokens is None:
                raise ExperimentError("required BenchFlow token telemetry is unavailable")
        n_tools = result.get("n_tool_calls", 0)
        if type(n_tools) is not int or cast(int, n_tools) < 0:
            raise ExperimentError("BenchFlow tool-call count is invalid")

        trajectory = {
            "schema_version": TRAJECTORY_SCHEMA,
            "events": events,
        }
        verifier = {
            "schema_version": VERIFIER_SCHEMA,
            "status": verifier_status,
            "reward": reward,
            "diagnostics_digest": diagnostics_digest,
        }
        artifacts["arena-benchflow-evidence.json"] = canonical_bytes(
            {
                "schema_version": "arena-benchflow-evidence@1",
                "task_digest": task_digest,
                "arm": arm,
                "skill_mode": skill_mode,
                "tool_names": tool_names,
                "unknown_tools": unknown_tools,
                "usage_tracking": _scrub_json(usage, secrets),
                "agent_result": _scrub_json(agent_result, secrets),
                "return_code": completed.returncode,
                "runtime_policy_digest": self.policy.digest,
                "effective_policy_digest": self.preparation["effective_policy_digest"],
                "environment_image_digest": self.preparation["environment_image_digest"],
            }
        )
        return InvocationCapture(
            classification=classification,
            reward=reward,
            adapter_exit_code=completed.returncode,
            error_code=error_code,
            stdout=stdout_text.encode("utf-8"),
            stderr=stderr_text.encode("utf-8"),
            trajectory=trajectory,
            verifier=verifier,
            metrics={
                "schema_version": METRICS_SCHEMA,
                "end_to_end_latency_ms": max(0, end_to_end_ms),
                "verifier_latency_ms": max(0, verifier_ms),
                "input_tokens": int(input_tokens or 0),
                "output_tokens": int(output_tokens or 0),
                "tool_tokens": int((cache_read or 0) + (cache_create or 0)),
                "cost_microunits": _cost_microunits(agent_result.get("cost_usd")),
                "cpu_time_ms": max(0, cpu_ms),
                "peak_memory_bytes": max(0, memory),
                "tool_call_count": cast(int, n_tools),
            },
            artifacts=artifacts,
            started_at=started_at,
            completed_at=finished_at,
        )


def summarize_paired_bundle(bundle_dir: Path | str) -> dict[str, object]:
    root = Path(bundle_dir)
    plan = _read_json(root / "plan-envelope.json", "plan envelope").get("payload")
    if not isinstance(plan, dict):
        raise ExperimentError("plan payload is absent")
    invocations = plan.get("invocations")
    if not isinstance(invocations, list):
        raise ExperimentError("plan invocations are absent")
    rows: list[dict[str, object]] = []
    task_digests: set[str] = set()
    for invocation in invocations:
        if not isinstance(invocation, dict):
            raise ExperimentError("plan invocation is invalid")
        invocation_id = cast(str, invocation["invocation_id"])
        outcome = _read_json(root / "invocations" / invocation_id / "outcome.json", "outcome")
        verifier = _read_json(root / "invocations" / invocation_id / "verifier.json", "verifier")
        evidence = _read_json(
            root / "invocations" / invocation_id / "artifacts" / "arena-benchflow-evidence.json",
            "BenchFlow evidence",
        )
        task_digests.add(require_sha256(evidence.get("task_digest"), "paired task digest"))
        rows.append(
            {
                "invocation_id": invocation_id,
                "pairing_key": invocation["pairing_key"],
                "repetition": invocation["repetition"],
                "arm": invocation["arm"],
                "classification": outcome["classification"],
                "reward": verifier["reward"],
                "input_tokens": _read_json(
                    root / "invocations" / invocation_id / "metrics.json", "metrics"
                )["input_tokens"],
                "output_tokens": _read_json(
                    root / "invocations" / invocation_id / "metrics.json", "metrics"
                )["output_tokens"],
                "cost_microunits": _read_json(
                    root / "invocations" / invocation_id / "metrics.json", "metrics"
                )["cost_microunits"],
                "latency_ms": _read_json(
                    root / "invocations" / invocation_id / "metrics.json", "metrics"
                )["end_to_end_latency_ms"],
            }
        )
    if len(task_digests) != 1:
        raise ExperimentError("baseline/candidate runs used different BenchFlow task digests")
    by_arm: dict[str, dict[str, object]] = {}
    for arm in ("baseline", "candidate"):
        selected = [row for row in rows if row["arm"] == arm]
        if len(selected) < 3:
            raise ExperimentError(f"paired result has fewer than three {arm} repetitions")
        rewards = [float(row["reward"]) for row in selected if row["reward"] is not None]
        by_arm[arm] = {
            "planned": len(selected),
            "scored": len(rewards),
            "successes": sum(row["classification"] == "succeeded" for row in selected),
            "mean_reward": (sum(rewards) / len(rewards)) if rewards else None,
            "total_input_tokens": sum(cast(int, row["input_tokens"]) for row in selected),
            "total_output_tokens": sum(cast(int, row["output_tokens"]) for row in selected),
            "total_cost_microunits": sum(cast(int, row["cost_microunits"]) for row in selected),
            "total_latency_ms": sum(cast(int, row["latency_ms"]) for row in selected),
        }
    baseline_mean = by_arm["baseline"]["mean_reward"]
    candidate_mean = by_arm["candidate"]["mean_reward"]
    lift = (
        float(candidate_mean) - float(baseline_mean)
        if baseline_mean is not None and candidate_mean is not None
        else None
    )
    result_without_digest: dict[str, object] = {
        "schema_version": PAIR_SUMMARY_SCHEMA,
        "experiment_id": cast(dict[str, object], plan["spec"])["experiment_id"],
        "plan_digest": plan["plan_digest"],
        "task_digest": next(iter(task_digests)),
        "rows": rows,
        "arms": by_arm,
        "observed_reward_lift": lift,
        "ranking_claim_allowed": False,
        "reason": "one task and three repetitions are runtime evidence, not a ranking estimate",
    }
    return {
        **result_without_digest,
        "result_digest": sha256_json(result_without_digest),
    }


__all__ = [
    "BenchFlowExperimentAdapter",
    "BenchFlowRuntimePolicy",
    "CATALOG_EVIDENCE_SCHEMA",
    "PAIR_SUMMARY_SCHEMA",
    "POLICY_SCHEMA",
    "compute_skill_artifact_digest",
    "fetch_github_model_catalog_evidence",
    "load_runtime_policy",
    "prepare_benchflow_runtime",
    "summarize_paired_bundle",
    "validate_catalog_evidence",
    "validate_preparation",
]
