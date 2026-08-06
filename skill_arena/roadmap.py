"""Validation helpers for the machine-readable SKILL.md Arena roadmap.

JSON Schema validates the shape and the evidence required for completed items.
This module adds graph invariants that JSON Schema cannot express: issue IDs are
unique, dependencies exist, dependency cycles are rejected, and an item's
coordination status agrees with the completion state of its dependencies.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

_ACTIVE_STATES = frozenset({"ready", "in_progress", "in_review", "human_gate", "done"})


class RoadmapReadError(ValueError):
    """The roadmap or schema could not be read as a JSON object."""


def load_json_object(path: Path | str) -> dict[str, Any]:
    """Load a UTF-8 JSON object and report file/parse failures explicitly."""
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RoadmapReadError(f"cannot read JSON object {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise RoadmapReadError(f"JSON root must be an object: {source}")
    return value


def _schema_error_path(error: Any) -> str:
    parts = [str(part) for part in error.absolute_path]
    return ".".join(parts) if parts else "<root>"


def _dependency_cycles(dependencies: Mapping[int, tuple[int, ...]]) -> list[str]:
    errors: list[str] = []
    visiting: set[int] = set()
    visited: set[int] = set()
    stack: list[int] = []

    def visit(issue: int) -> None:
        if issue in visited:
            return
        if issue in visiting:
            start = stack.index(issue)
            cycle = stack[start:] + [issue]
            errors.append("dependency cycle: " + " -> ".join(f"#{item}" for item in cycle))
            return

        visiting.add(issue)
        stack.append(issue)
        for dependency in dependencies.get(issue, ()):
            if dependency in dependencies:
                visit(dependency)
        stack.pop()
        visiting.remove(issue)
        visited.add(issue)

    for issue in sorted(dependencies):
        visit(issue)
    return errors


def validate_roadmap(
    document: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> list[str]:
    """Return deterministic schema and dependency/status diagnostics."""
    errors = [
        f"schema {_schema_error_path(error)}: {error.message}"
        for error in sorted(
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            ).iter_errors(document),
            key=lambda item: (list(item.absolute_path), item.message),
        )
    ]

    raw_items = document.get("items")
    if not isinstance(raw_items, list):
        return sorted(set(errors))

    items: dict[int, Mapping[str, Any]] = {}
    duplicate_issues: set[int] = set()
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            continue
        issue = raw_item.get("issue")
        if not isinstance(issue, int) or isinstance(issue, bool):
            continue
        if issue in items:
            duplicate_issues.add(issue)
            errors.append(f"items[{index}]: duplicate issue #{issue}")
            continue
        items[issue] = raw_item

    dependencies: dict[int, tuple[int, ...]] = {}
    for issue, item in sorted(items.items()):
        raw_dependencies = item.get("dependencies")
        if not isinstance(raw_dependencies, list):
            dependencies[issue] = ()
            continue
        valid_dependencies = tuple(
            dependency
            for dependency in raw_dependencies
            if isinstance(dependency, int) and not isinstance(dependency, bool)
        )
        dependencies[issue] = valid_dependencies
        for dependency in valid_dependencies:
            if dependency == issue:
                errors.append(f"issue #{issue}: self-dependency is forbidden")
            elif dependency not in items:
                errors.append(f"issue #{issue}: unknown dependency #{dependency}")

    if not duplicate_issues:
        errors.extend(_dependency_cycles(dependencies))

    statuses = {
        issue: item.get("status")
        for issue, item in items.items()
        if isinstance(item.get("status"), str)
    }
    for issue, item in sorted(items.items()):
        status = item.get("status")
        if not isinstance(status, str):
            continue
        unresolved = [
            dependency
            for dependency in dependencies.get(issue, ())
            if statuses.get(dependency) != "done"
        ]
        if status == "blocked" and not unresolved:
            errors.append(f"issue #{issue}: blocked but has no unresolved dependency")
        elif status in _ACTIVE_STATES and unresolved:
            blocked_by = ", ".join(f"#{dependency}" for dependency in unresolved)
            errors.append(
                f"issue #{issue}: status {status!r} while dependencies are unresolved: {blocked_by}"
            )

    return sorted(set(errors))


def validate_roadmap_files(
    roadmap_path: Path | str,
    schema_path: Path | str,
) -> list[str]:
    """Load and validate a roadmap and its JSON Schema from disk."""
    document = load_json_object(roadmap_path)
    schema = load_json_object(schema_path)
    return validate_roadmap(document, schema)
