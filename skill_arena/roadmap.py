"""Validation helpers for the machine-readable SKILL.md Arena roadmap.

JSON Schema validates the shape and the evidence required for completed items.
This module adds graph invariants that JSON Schema cannot express: issue IDs are
unique, dependencies exist, dependency cycles are rejected, coordination state
matches dependency state, and every completed roadmap row is an exact projection
of repository-local landing evidence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

_ACTIVE_STATES = frozenset({"ready", "in_progress", "in_review", "human_gate", "done"})


class RoadmapReadError(ValueError):
    """The roadmap, schema, or delivery authority could not be read."""


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


def _issue_rows(
    raw_rows: object,
    *,
    id_field: str,
    label: str,
) -> tuple[dict[int, Mapping[str, Any]], list[str]]:
    if not isinstance(raw_rows, list):
        return {}, [f"{label}: rows must be an array"]

    rows: dict[int, Mapping[str, Any]] = {}
    errors: list[str] = []
    for index, raw_row in enumerate(raw_rows):
        if not isinstance(raw_row, Mapping):
            continue
        issue = raw_row.get(id_field)
        if not isinstance(issue, int) or isinstance(issue, bool):
            continue
        if issue in rows:
            errors.append(f"{label}[{index}]: duplicate issue #{issue}")
            continue
        rows[issue] = raw_row
    return rows, errors


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

    items, row_errors = _issue_rows(
        document.get("items"), id_field="issue", label="items"
    )
    errors.extend(row_errors)
    if not items:
        return sorted(set(errors))

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

    if not row_errors:
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
                f"issue #{issue}: status {status!r} while dependencies are unresolved:"
                f" {blocked_by}"
            )

    return sorted(set(errors))


def validate_delivery_projection(
    roadmap: Mapping[str, Any],
    landing_authority: Mapping[str, Any],
) -> list[str]:
    """Require roadmap completion to exactly project landing-evidence authority."""
    roadmap_rows, roadmap_errors = _issue_rows(
        roadmap.get("items"), id_field="issue", label="roadmap items"
    )
    authority_rows, authority_errors = _issue_rows(
        landing_authority.get("work_items"),
        id_field="issue_number",
        label="landing work_items",
    )
    errors = [*roadmap_errors, *authority_errors]

    for issue, row in sorted(roadmap_rows.items()):
        status = row.get("status")
        authority = authority_rows.get(issue)
        authority_completed = (
            authority is not None and authority.get("status") == "completed"
        )

        if status != "done":
            if authority_completed:
                errors.append(
                    f"issue #{issue}: landing authority is completed but roadmap"
                    f" status is {status!r}"
                )
            continue

        if authority is None:
            errors.append(
                f"issue #{issue}: roadmap is done but landing authority has no row"
            )
            continue
        if not authority_completed:
            errors.append(
                f"issue #{issue}: roadmap is done but landing authority status is"
                f" {authority.get('status')!r}"
            )
            continue

        roadmap_landing = row.get("landing")
        authority_landing = authority.get("landing")
        if not isinstance(roadmap_landing, Mapping) or not isinstance(
            authority_landing, Mapping
        ):
            errors.append(f"issue #{issue}: completed projection lacks landing data")
            continue

        authority_test = authority_landing.get("test_evidence")
        authority_test_digest = (
            authority_test.get("digest")
            if isinstance(authority_test, Mapping)
            else None
        )
        expected = {
            "commit_sha": authority_landing.get("commit_sha"),
            "paths_digest": authority_landing.get("changed_paths_digest"),
            "test_evidence_digest": authority_test_digest,
        }
        for field, expected_value in expected.items():
            actual_value = roadmap_landing.get(field)
            if actual_value != expected_value:
                errors.append(
                    f"issue #{issue}: roadmap landing {field} does not match"
                    f" authority: {actual_value!r} != {expected_value!r}"
                )

        authority_level = authority.get("evidence_level")
        if row.get("evidence") != authority_level:
            errors.append(
                f"issue #{issue}: roadmap evidence does not match authority:"
                f" {row.get('evidence')!r} != {authority_level!r}"
            )

    return sorted(set(errors))


def validate_roadmap_files(
    roadmap_path: Path | str,
    schema_path: Path | str,
    landing_evidence_path: Path | str | None = None,
) -> list[str]:
    """Load and validate roadmap shape, graph state, and delivery projection."""
    document = load_json_object(roadmap_path)
    schema = load_json_object(schema_path)
    errors = validate_roadmap(document, schema)
    if landing_evidence_path is not None:
        authority = load_json_object(landing_evidence_path)
        errors.extend(validate_delivery_projection(document, authority))
    return sorted(set(errors))
