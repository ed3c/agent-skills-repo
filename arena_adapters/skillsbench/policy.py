from __future__ import annotations

import re
from typing import Any, Mapping, cast

from .common import read_json_object, safe_repository_path
from .models import (
    ImportPolicy,
    SkillsBenchAdapterError,
    TaskSelection,
    UpstreamPolicy,
)

POLICY_SCHEMA_VERSION = "skillsbench-import-policy@1"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TASK_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_ROLES = frozenset({"code", "document-data", "skill-composition"})


def _exact_keys(value: Mapping[str, object], allowed: set[str], label: str) -> None:
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise SkillsBenchAdapterError(f"{label} has unsupported fields: {unexpected}")


def load_policy(path: object) -> ImportPolicy:
    raw = read_json_object(cast(Any, path))
    _exact_keys(raw, {"schema_version", "upstream", "allowed_licenses", "tasks"}, "policy")
    if raw.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise SkillsBenchAdapterError(
            f"unsupported policy schema: {raw.get('schema_version')!r}"
        )

    upstream_raw = raw.get("upstream")
    if not isinstance(upstream_raw, dict):
        raise SkillsBenchAdapterError("policy.upstream must be an object")
    _exact_keys(
        upstream_raw,
        {"repository", "commit", "license", "license_path", "benchflow_requirement"},
        "policy.upstream",
    )
    repository = upstream_raw.get("repository")
    commit = upstream_raw.get("commit")
    license_name = upstream_raw.get("license")
    license_path = safe_repository_path(upstream_raw.get("license_path"), "license_path")
    benchflow_requirement = upstream_raw.get("benchflow_requirement")
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
        raise SkillsBenchAdapterError("upstream repository must use owner/name form")
    if not isinstance(commit, str) or not FULL_SHA_RE.fullmatch(commit):
        raise SkillsBenchAdapterError("upstream commit must be a full lowercase SHA")
    if not isinstance(license_name, str) or not license_name:
        raise SkillsBenchAdapterError("upstream license must be a non-empty string")
    if not isinstance(benchflow_requirement, str) or not benchflow_requirement:
        raise SkillsBenchAdapterError("benchflow_requirement must be a non-empty string")

    allowed_raw = raw.get("allowed_licenses")
    if not isinstance(allowed_raw, list) or not allowed_raw or any(
        not isinstance(item, str) or not item for item in allowed_raw
    ):
        raise SkillsBenchAdapterError("allowed_licenses must be a non-empty string list")
    allowed = tuple(cast(list[str], allowed_raw))
    if len(set(allowed)) != len(allowed):
        raise SkillsBenchAdapterError("allowed_licenses contains duplicates")
    if license_name not in allowed:
        raise SkillsBenchAdapterError(f"upstream license is not allowed: {license_name!r}")

    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise SkillsBenchAdapterError("policy.tasks must be a non-empty array")
    tasks: list[TaskSelection] = []
    seen_ids: set[str] = set()
    roles: set[str] = set()
    for index, item in enumerate(tasks_raw):
        if not isinstance(item, dict):
            raise SkillsBenchAdapterError(f"tasks[{index}] must be an object")
        _exact_keys(
            item,
            {
                "task_id",
                "path",
                "role",
                "expected_network_mode",
                "expected_skill_names",
                "required_files",
            },
            f"tasks[{index}]",
        )
        task_id = item.get("task_id")
        task_path = safe_repository_path(item.get("path"), f"tasks[{index}].path")
        role = item.get("role")
        network = item.get("expected_network_mode")
        skills_raw = item.get("expected_skill_names")
        required_raw = item.get("required_files")
        if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
            raise SkillsBenchAdapterError(f"tasks[{index}].task_id is invalid")
        if task_path != f"tasks/{task_id}":
            raise SkillsBenchAdapterError(
                f"tasks[{index}] path must be tasks/{task_id}: {task_path!r}"
            )
        if task_id in seen_ids:
            raise SkillsBenchAdapterError(f"duplicate selected task: {task_id}")
        seen_ids.add(task_id)
        if not isinstance(role, str) or role not in ALLOWED_ROLES:
            raise SkillsBenchAdapterError(f"tasks[{index}].role is invalid: {role!r}")
        roles.add(role)
        if network not in {"public", "no-network"}:
            raise SkillsBenchAdapterError(
                f"tasks[{index}].expected_network_mode is invalid: {network!r}"
            )
        if not isinstance(skills_raw, list) or any(
            not isinstance(skill, str) or not TASK_ID_RE.fullmatch(skill)
            for skill in skills_raw
        ):
            raise SkillsBenchAdapterError(
                f"tasks[{index}].expected_skill_names must contain canonical names"
            )
        skill_names = tuple(cast(list[str], skills_raw))
        if tuple(sorted(skill_names)) != skill_names or len(set(skill_names)) != len(skill_names):
            raise SkillsBenchAdapterError(
                f"tasks[{index}].expected_skill_names must be sorted and unique"
            )
        if not isinstance(required_raw, list) or not required_raw:
            raise SkillsBenchAdapterError(
                f"tasks[{index}].required_files must be a non-empty array"
            )
        required = tuple(
            safe_repository_path(value, f"tasks[{index}].required_files")
            for value in required_raw
        )
        if tuple(sorted(required)) != required or len(set(required)) != len(required):
            raise SkillsBenchAdapterError(
                f"tasks[{index}].required_files must be sorted and unique"
            )
        tasks.append(
            TaskSelection(
                task_id=task_id,
                path=task_path,
                role=cast(Any, role),
                expected_network_mode=cast(str, network),
                expected_skill_names=skill_names,
                required_files=required,
            )
        )

    if [task.task_id for task in tasks] != sorted(task.task_id for task in tasks):
        raise SkillsBenchAdapterError("policy.tasks must be sorted by task_id")
    missing_roles = sorted(ALLOWED_ROLES - roles)
    if missing_roles:
        raise SkillsBenchAdapterError(
            f"selected tasks do not cover required Arena roles: {missing_roles}"
        )
    upstream = UpstreamPolicy(
        repository=repository,
        commit=commit,
        license=license_name,
        license_path=license_path,
        benchflow_requirement=benchflow_requirement,
    )
    return ImportPolicy(upstream, allowed, tuple(tasks))
