"""Deterministic export from repository-native skills to portable Agent Skills.

The repository's historical source format is ``skills/<id>/skills.md`` with
WHY/HOW/WHEN/WHEN NOT sections. The external interchange boundary is the Agent
Skills directory format: a hyphenated directory with a root ``SKILL.md`` and
YAML frontmatter.

Exports are additive and byte-preserving for behavior content. The exporter
adds frontmatter, maps underscore IDs to hyphenated names, and copies files from
``references/``, ``scripts/``, and ``assets/`` without rewriting their bytes.
Every transformation is recorded in ``export-manifest.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, cast

import yaml
from jsonschema import Draft202012Validator

EXPORT_SCHEMA_VERSION = "agent-skills-export@1"
REGISTRY_SCHEMA_VERSION = "agent-skills-registry@1"
POLICY_SCHEMA_VERSION = "agent-skills-export-policy@1"
BEHAVIOR_DIRECTORIES = ("assets", "references", "scripts")
ROOT_BEHAVIOR_FILES = ("cases.json",)
REQUIRED_SECTIONS = ("WHY", "HOW", "WHEN", "WHEN NOT")
SOURCE_ID_RE = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")
PORTABLE_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class AgentSkillsExportError(ValueError):
    """A source skill, export policy, or generated bundle is invalid."""


@dataclass(frozen=True)
class ExportPolicy:
    source_license: str
    compatibility: str
    spec_repository: str
    spec_commit: str
    spec_license: str
    validator: str
    validator_subdirectory: str


@dataclass(frozen=True)
class Lifecycle:
    status: str
    production_routable: bool
    source: str


@dataclass(frozen=True)
class ExportedSkill:
    source_skill_id: str
    portable_name: str
    source_artifact_digest: str
    portable_artifact_digest: str
    lifecycle_status: str
    production_routable: bool
    manifest_path: str


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgentSkillsExportError(f"JSON input is unreadable: {path}: {exc}") from exc


def load_policy(path: Path | str) -> ExportPolicy:
    raw = _read_json(Path(path))
    if not isinstance(raw, dict):
        raise AgentSkillsExportError("export policy must be a JSON object")
    if raw.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise AgentSkillsExportError(
            f"unsupported export policy schema: {raw.get('schema_version')!r}"
        )
    upstream = raw.get("upstream_agent_skills")
    if not isinstance(upstream, dict):
        raise AgentSkillsExportError("export policy lacks upstream_agent_skills")
    required = {
        "source_license": raw.get("source_license"),
        "compatibility": raw.get("compatibility"),
        "spec_repository": upstream.get("repository"),
        "spec_commit": upstream.get("commit"),
        "spec_license": upstream.get("license"),
        "validator": upstream.get("validator"),
        "validator_subdirectory": upstream.get("validator_subdirectory"),
    }
    for field, value in required.items():
        if not isinstance(value, str) or not value.strip():
            raise AgentSkillsExportError(f"export policy field is missing: {field}")
    if not re.fullmatch(r"[0-9a-f]{40}", cast(str, required["spec_commit"])):
        raise AgentSkillsExportError("upstream Agent Skills commit must be a full SHA")
    compatibility = cast(str, required["compatibility"])
    if len(compatibility) > 500:
        raise AgentSkillsExportError("compatibility text exceeds Agent Skills limit")
    return ExportPolicy(**cast(dict[str, str], required))


def portable_name(source_skill_id: str) -> str:
    if not SOURCE_ID_RE.fullmatch(source_skill_id):
        raise AgentSkillsExportError(
            "source skill id must contain lowercase letters, digits, underscores,"
            f" or single hyphens: {source_skill_id!r}"
        )
    converted = source_skill_id.replace("_", "-")
    if not PORTABLE_NAME_RE.fullmatch(converted) or len(converted) > 64:
        raise AgentSkillsExportError(
            f"source skill id cannot map to a valid Agent Skills name: {source_skill_id!r}"
        )
    return converted


def parse_sections(text: str) -> dict[str, str]:
    """Parse exactly one ordered WHY/HOW/WHEN/WHEN NOT block."""
    marker_re = re.compile(r"^(WHY|HOW|WHEN|WHEN NOT):(?:\s*(.*))?$", re.MULTILINE)
    matches = list(marker_re.finditer(text))
    names = [match.group(1) for match in matches]
    if names != list(REQUIRED_SECTIONS):
        raise AgentSkillsExportError(
            "skills.md must contain exactly one ordered WHY/HOW/WHEN/WHEN NOT block;"
            f" found {names}"
        )

    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        start = match.start(2) if match.group(2) is not None else match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if not content:
            raise AgentSkillsExportError(f"skills.md section is empty: {match.group(1)}")
        sections[match.group(1)] = content
    return sections


def build_description(sections: Mapping[str, str]) -> str:
    why = sections["WHY"].replace("\n", " ").strip()
    when = sections["WHEN"].replace("\n", " ").strip()
    description = f"{why} Use when: {when}"
    if not 1 <= len(description) <= 1024:
        raise AgentSkillsExportError(
            f"generated Agent Skills description has invalid length: {len(description)}"
        )
    return description


def _ensure_inside(root: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise AgentSkillsExportError(
            f"skill resource escapes its source directory: {candidate}"
        ) from exc


def _regular_files(skill_dir: Path) -> list[Path]:
    """Return source behavior files and reject links/special files fail-closed."""
    resolved_root = skill_dir.resolve(strict=True)
    files = [skill_dir / "skills.md"]
    for file_name in ROOT_BEHAVIOR_FILES:
        root_file = skill_dir / file_name
        if not root_file.exists():
            continue
        if root_file.is_symlink() or not root_file.is_file():
            raise AgentSkillsExportError(
                f"root behavior resource must be a regular file: {root_file}"
            )
        _ensure_inside(resolved_root, root_file.resolve(strict=True))
        files.append(root_file)
    for directory_name in BEHAVIOR_DIRECTORIES:
        directory = skill_dir / directory_name
        if not directory.exists():
            continue
        if directory.is_symlink() or not directory.is_dir():
            raise AgentSkillsExportError(
                f"behavior resource root must be a real directory: {directory}"
            )
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise AgentSkillsExportError(f"symlink resources are not exportable: {path}")
            if path.is_dir():
                continue
            if not path.is_file():
                raise AgentSkillsExportError(
                    f"non-regular resources are not exportable: {path}"
                )
            _ensure_inside(resolved_root, path.resolve(strict=True))
            files.append(path)
    root_skill = skill_dir / "skills.md"
    if root_skill.is_symlink() or not root_skill.is_file():
        raise AgentSkillsExportError(f"source skill is missing a regular skills.md: {skill_dir}")
    return files


def _file_entries(root: Path, files: Sequence[Path]) -> list[dict[str, str]]:
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(files, key=lambda item: item.relative_to(root).as_posix())
    ]


def source_artifact_digest(skill_dir: Path | str) -> str:
    skill = Path(skill_dir)
    entries = _file_entries(skill, _regular_files(skill))
    return sha256_bytes(canonical_bytes({"files": entries}))


def _read_optional_mapping(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise AgentSkillsExportError(f"metadata input must be a regular file: {path}")
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise AgentSkillsExportError(f"expected a JSON object: {path}")
    return cast(dict[str, object], raw)


def _repository_lifecycle(skill: Path) -> Lifecycle | None:
    """Read repository lifecycle records without inventing routability.

    Some historical skills predate per-skill ``status.json`` and keep their
    lifecycle in ``data/lifecycle``. The registry may describe a candidate
    state, while promotion records still require a human admit. Neither file
    exposes the explicit boolean authority required to route production work,
    so this fallback is deliberately non-routable. A future promotion must
    materialize a per-skill status file (or a new versioned authority with an
    explicit boolean) before an export can become routable.
    """
    if skill.parent.name != "skills":
        return None
    repository_root = skill.parent.parent
    registry_path = repository_root / "data/lifecycle/skill_optimization_registry.json"
    registry = _read_optional_mapping(registry_path)
    if registry is None:
        return None
    skills = registry.get("skills")
    if not isinstance(skills, list):
        raise AgentSkillsExportError(
            f"lifecycle registry lacks a skills list: {registry_path}"
        )
    matches = [
        entry
        for entry in skills
        if isinstance(entry, dict) and entry.get("skill_id") == skill.name
    ]
    if len(matches) > 1:
        raise AgentSkillsExportError(
            f"lifecycle registry has duplicate entries for {skill.name!r}"
        )
    if not matches:
        return None
    state = matches[0].get("current_status")
    if not isinstance(state, str) or not state:
        raise AgentSkillsExportError(
            f"lifecycle registry entry lacks current_status: {skill.name!r}"
        )

    source = "data/lifecycle/skill_optimization_registry.json"
    promotion_path = repository_root / "data/lifecycle/promotion_records.json"
    promotion = _read_optional_mapping(promotion_path)
    if promotion is not None:
        records = promotion.get("records")
        if not isinstance(records, list):
            raise AgentSkillsExportError(
                f"promotion records lack a records list: {promotion_path}"
            )
        matching_records = [
            record
            for record in records
            if isinstance(record, dict) and record.get("skill_id") == skill.name
        ]
        if len(matching_records) > 1:
            raise AgentSkillsExportError(
                f"promotion records have duplicate entries for {skill.name!r}"
            )
        if matching_records:
            promotion_status = matching_records[0].get("promotion_status")
            human_admit = matching_records[0].get("human_admit")
            if not isinstance(promotion_status, str) or not promotion_status:
                raise AgentSkillsExportError(
                    f"promotion record lacks promotion_status: {skill.name!r}"
                )
            if not isinstance(human_admit, str) or not human_admit:
                raise AgentSkillsExportError(
                    f"promotion record lacks human_admit: {skill.name!r}"
                )
            source += "+data/lifecycle/promotion_records.json"

    return Lifecycle(state, False, source)


def lifecycle_for(skill_dir: Path | str) -> Lifecycle:
    skill = Path(skill_dir)
    status = _read_optional_mapping(skill / "status.json")
    if status is not None:
        state = status.get("status")
        routable = status.get("production_routable")
        if not isinstance(state, str) or not state:
            raise AgentSkillsExportError(f"status.json lacks status: {skill}")
        if not isinstance(routable, bool):
            raise AgentSkillsExportError(
                f"status.json lacks boolean production_routable: {skill}"
            )
        return Lifecycle(state, routable, "status.json")

    manifest = _read_optional_mapping(skill / "manifest.json")
    if manifest is not None:
        receipt = manifest.get("qualification_receipt_id")
        if receipt == "pending-qualification":
            return Lifecycle("pending-qualification", False, "manifest.json")
        if isinstance(receipt, str) and receipt:
            return Lifecycle(
                "qualification-receipt-present-unadmitted", False, "manifest.json"
            )

    repository_lifecycle = _repository_lifecycle(skill)
    if repository_lifecycle is not None:
        return repository_lifecycle
    return Lifecycle("unverified", False, "inferred-no-status")


def source_version(skill_dir: Path | str) -> str:
    manifest = _read_optional_mapping(Path(skill_dir) / "manifest.json")
    if manifest is not None and isinstance(manifest.get("skill_version"), str):
        return cast(str, manifest["skill_version"])
    return "unversioned"


def _yaml_string(value: str) -> str:
    # JSON double-quoted strings are valid YAML and deterministic across PyYAML versions.
    return json.dumps(value, ensure_ascii=False)


def render_skill_md(
    *,
    source_body: bytes,
    name: str,
    description: str,
    policy: ExportPolicy,
    source_skill_id: str,
    source_digest: str,
    lifecycle: Lifecycle,
    version: str,
) -> bytes:
    try:
        source_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentSkillsExportError("skills.md must be UTF-8") from exc
    metadata = {
        "ed3c-export-schema": EXPORT_SCHEMA_VERSION,
        "ed3c-lifecycle-status": lifecycle.status,
        "ed3c-production-routable": str(lifecycle.production_routable).lower(),
        "ed3c-source-artifact-digest": source_digest,
        "ed3c-source-skill-id": source_skill_id,
        "ed3c-source-version": version,
    }
    lines = [
        "---",
        f"name: {_yaml_string(name)}",
        f"description: {_yaml_string(description)}",
        f"license: {_yaml_string(policy.source_license)}",
        f"compatibility: {_yaml_string(policy.compatibility)}",
        "metadata:",
    ]
    for key, value in sorted(metadata.items()):
        lines.append(f"  {key}: {_yaml_string(value)}")
    lines.extend(["---", ""])
    return "\n".join(lines).encode("utf-8") + source_body


def _parse_frontmatter(skill_md: bytes) -> tuple[dict[str, object], bytes]:
    if not skill_md.startswith(b"---\n"):
        raise AgentSkillsExportError("SKILL.md must begin with YAML frontmatter")
    closing = skill_md.find(b"\n---\n", 4)
    if closing < 0:
        raise AgentSkillsExportError("SKILL.md frontmatter is not closed")
    raw = skill_md[4:closing]
    try:
        loaded = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise AgentSkillsExportError(f"SKILL.md frontmatter is invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise AgentSkillsExportError("SKILL.md frontmatter must be a mapping")
    body = skill_md[closing + len(b"\n---\n") :]
    return cast(dict[str, object], loaded), body


def validate_portable_skill(skill_dir: Path | str) -> dict[str, object]:
    """Validate the stable subset of the Agent Skills specification locally."""
    skill = Path(skill_dir)
    if skill.is_symlink() or not skill.is_dir():
        raise AgentSkillsExportError(f"portable skill directory is absent: {skill}")
    name = skill.name
    if not PORTABLE_NAME_RE.fullmatch(name) or len(name) > 64:
        raise AgentSkillsExportError(f"invalid portable skill directory name: {name!r}")
    skill_md_path = skill / "SKILL.md"
    if skill_md_path.is_symlink() or not skill_md_path.is_file():
        raise AgentSkillsExportError(f"portable skill lacks regular SKILL.md: {skill}")
    frontmatter, body = _parse_frontmatter(skill_md_path.read_bytes())
    declared_name = frontmatter.get("name")
    description = frontmatter.get("description")
    if declared_name != name:
        raise AgentSkillsExportError(
            f"SKILL.md name must match parent directory: {declared_name!r} != {name!r}"
        )
    if not isinstance(description, str) or not 1 <= len(description) <= 1024:
        raise AgentSkillsExportError("SKILL.md description must contain 1-1024 characters")
    if "Use when:" not in description:
        raise AgentSkillsExportError(
            "SKILL.md description must include an explicit activation boundary"
        )
    license_value = frontmatter.get("license")
    if not isinstance(license_value, str) or not license_value:
        raise AgentSkillsExportError("SKILL.md license must be a non-empty string")
    compatibility = frontmatter.get("compatibility")
    if not isinstance(compatibility, str) or not 1 <= len(compatibility) <= 500:
        raise AgentSkillsExportError(
            "SKILL.md compatibility must contain 1-500 characters"
        )
    metadata = frontmatter.get("metadata")
    if not isinstance(metadata, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in metadata.items()
    ):
        raise AgentSkillsExportError("SKILL.md metadata must map strings to strings")
    if not body.strip():
        raise AgentSkillsExportError("SKILL.md body is empty")
    return frontmatter


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _portable_files(skill_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(skill_dir.rglob("*")):
        if path.is_symlink():
            raise AgentSkillsExportError(f"generated export contains a symlink: {path}")
        if path.is_dir() or path.name == "export-manifest.json":
            continue
        if not path.is_file():
            raise AgentSkillsExportError(f"generated export contains special file: {path}")
        files.append(path)
    return files


def _portable_digest_and_entries(skill_dir: Path) -> tuple[str, list[dict[str, str]]]:
    entries = _file_entries(skill_dir, _portable_files(skill_dir))
    return sha256_bytes(canonical_bytes({"files": entries})), entries


def _manifest(
    *,
    source_skill_id: str,
    portable: str,
    source_digest: str,
    portable_digest: str,
    portable_files: list[dict[str, str]],
    lifecycle: Lifecycle,
    version: str,
    policy: ExportPolicy,
    copied_resources: Sequence[str],
) -> dict[str, object]:
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "source": {
            "skill_id": source_skill_id,
            "directory": f"skills/{source_skill_id}",
            "version": version,
            "artifact_digest": source_digest,
            "lifecycle_status": lifecycle.status,
            "lifecycle_source": lifecycle.source,
            "production_routable": lifecycle.production_routable,
        },
        "portable": {
            "name": portable,
            "directory": f"dist/agent-skills/{portable}",
            "artifact_digest": portable_digest,
            "files": portable_files,
        },
        "upstream_conformance": {
            "repository": policy.spec_repository,
            "commit": policy.spec_commit,
            "license": policy.spec_license,
            "validator": policy.validator,
            "validator_subdirectory": policy.validator_subdirectory,
            "required_in_ci": True,
        },
        "transformations": [
            {
                "field": "directory-name",
                "kind": "deterministic-lossless",
                "detail": f"{source_skill_id} -> {portable}; underscores map to hyphens",
            },
            {
                "field": "SKILL.md-frontmatter",
                "kind": "additive",
                "detail": (
                    "Generated from WHY and WHEN plus license, compatibility,"
                    " digest, and lifecycle metadata"
                ),
            },
            {
                "field": "SKILL.md-body",
                "kind": "byte-preserving",
                "detail": "Body bytes after frontmatter are identical to source skills.md",
            },
            {
                "field": "behavior-resources",
                "kind": "byte-preserving",
                "detail": "Copied without rewriting: " + ", ".join(copied_resources),
            },
            {
                "field": "lifecycle",
                "kind": "metadata-only",
                "detail": "Conformance never promotes, qualifies, or unquarantines a skill",
            },
        ],
        "losses": [],
    }


def export_one(
    source_skill_dir: Path, destination_root: Path, policy: ExportPolicy
) -> ExportedSkill:
    source_id = source_skill_dir.name
    portable = portable_name(source_id)
    # Validate all source paths before reading behavior bytes.
    source_files = _regular_files(source_skill_dir)
    source_body_path = source_skill_dir / "skills.md"
    source_body = source_body_path.read_bytes()
    try:
        source_text = source_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentSkillsExportError(f"skills.md must be UTF-8: {source_body_path}") from exc
    sections = parse_sections(source_text)
    description = build_description(sections)
    source_digest = source_artifact_digest(source_skill_dir)
    lifecycle = lifecycle_for(source_skill_dir)
    version = source_version(source_skill_dir)

    destination = destination_root / portable
    destination.mkdir(parents=True, exist_ok=False)
    _write_bytes(
        destination / "SKILL.md",
        render_skill_md(
            source_body=source_body,
            name=portable,
            description=description,
            policy=policy,
            source_skill_id=source_id,
            source_digest=source_digest,
            lifecycle=lifecycle,
            version=version,
        ),
    )

    copied_resources: list[str] = []
    for source in source_files:
        relative = source.relative_to(source_skill_dir)
        if relative == Path("skills.md"):
            continue
        target = destination / relative
        _write_bytes(target, source.read_bytes())
        copied_resources.append(relative.as_posix())

    validate_portable_skill(destination)
    portable_digest, portable_files = _portable_digest_and_entries(destination)
    manifest = _manifest(
        source_skill_id=source_id,
        portable=portable,
        source_digest=source_digest,
        portable_digest=portable_digest,
        portable_files=portable_files,
        lifecycle=lifecycle,
        version=version,
        policy=policy,
        copied_resources=copied_resources,
    )
    _write_bytes(
        destination / "export-manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n",
    )
    return ExportedSkill(
        source_id,
        portable,
        source_digest,
        portable_digest,
        lifecycle.status,
        lifecycle.production_routable,
        f"dist/agent-skills/{portable}/export-manifest.json",
    )


def discover_source_skills(source_root: Path | str) -> list[Path]:
    root = Path(source_root)
    if root.is_symlink() or not root.is_dir():
        raise AgentSkillsExportError(f"skills source directory is absent: {root}")
    entries = list(root.iterdir())
    symlinks = [path for path in entries if path.is_symlink()]
    if symlinks:
        raise AgentSkillsExportError(
            "symlink entries are not allowed under the skills source root: "
            + ", ".join(path.name for path in sorted(symlinks))
        )
    skills = sorted(
        (path for path in entries if path.is_dir() and (path / "skills.md").exists()),
        key=lambda path: path.name,
    )
    if not skills:
        raise AgentSkillsExportError("no repository-native skills found")
    names: dict[str, str] = {}
    for skill in skills:
        mapped = portable_name(skill.name)
        previous = names.get(mapped)
        if previous is not None:
            raise AgentSkillsExportError(
                f"portable name collision: {previous!r} and {skill.name!r} -> {mapped!r}"
            )
        names[mapped] = skill.name
    return skills


def _registry(skills: Sequence[ExportedSkill], policy: ExportPolicy) -> dict[str, object]:
    rows = [
        {
            "source_skill_id": skill.source_skill_id,
            "portable_name": skill.portable_name,
            "source_artifact_digest": skill.source_artifact_digest,
            "portable_artifact_digest": skill.portable_artifact_digest,
            "lifecycle_status": skill.lifecycle_status,
            "production_routable": skill.production_routable,
            "export_manifest": skill.manifest_path,
        }
        for skill in sorted(skills, key=lambda item: item.portable_name)
    ]
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "upstream_agent_skills": {
            "repository": policy.spec_repository,
            "commit": policy.spec_commit,
            "license": policy.spec_license,
            "validator": policy.validator,
        },
        "skills": rows,
        "registry_digest": sha256_bytes(canonical_bytes({"skills": rows})),
    }


def generate_export_tree(
    source_root: Path | str,
    destination_root: Path | str,
    policy_path: Path | str,
) -> list[ExportedSkill]:
    destination = Path(destination_root)
    if destination.exists():
        raise AgentSkillsExportError(f"destination must not already exist: {destination}")
    destination.mkdir(parents=True)
    policy = load_policy(policy_path)
    exported = [
        export_one(source, destination, policy)
        for source in discover_source_skills(source_root)
    ]
    registry = _registry(exported, policy)
    _write_bytes(
        destination / "registry.json",
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n",
    )
    return exported


def _tree(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        raise AgentSkillsExportError(f"export tree is absent: {root}")
    result: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise AgentSkillsExportError(f"export tree contains a symlink: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise AgentSkillsExportError(f"export tree contains special file: {path}")
        result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def compare_export_trees(expected: Path | str, actual: Path | str) -> list[str]:
    expected_tree = _tree(Path(expected))
    actual_tree = _tree(Path(actual))
    diagnostics: list[str] = []
    for path in sorted(expected_tree.keys() - actual_tree.keys()):
        diagnostics.append(f"missing export file: {path}")
    for path in sorted(actual_tree.keys() - expected_tree.keys()):
        diagnostics.append(f"unexpected export file: {path}")
    for path in sorted(expected_tree.keys() & actual_tree.keys()):
        if expected_tree[path] != actual_tree[path]:
            diagnostics.append(f"stale export file: {path}")
    return diagnostics


def _schema_diagnostics(instance: object, schema: object, label: str) -> list[str]:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(instance),
        key=lambda error: list(error.absolute_path),
    )
    return [
        f"{label}:{'.'.join(str(part) for part in error.absolute_path) or '<root>'}:"
        f" {error.message}"
        for error in errors
    ]


def validate_export_manifests(export_root: Path | str, schema_path: Path | str) -> None:
    root = Path(export_root)
    schema = _read_json(Path(schema_path))
    diagnostics: list[str] = []
    registry_path = root / "registry.json"
    registry = _read_json(registry_path)
    diagnostics.extend(_schema_diagnostics(registry, schema, "registry.json"))
    if not isinstance(registry, dict):
        raise AgentSkillsExportError("registry.json must be an object")
    rows = registry.get("skills")
    if not isinstance(rows, list):
        raise AgentSkillsExportError("registry.json lacks skills")

    seen_names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            diagnostics.append("registry skill row is not an object")
            continue
        name = row.get("portable_name")
        if not isinstance(name, str):
            diagnostics.append("registry skill row lacks portable_name")
            continue
        if name in seen_names:
            diagnostics.append(f"duplicate portable_name in registry: {name}")
        seen_names.add(name)
        skill_dir = root / name
        try:
            frontmatter = validate_portable_skill(skill_dir)
        except AgentSkillsExportError as exc:
            diagnostics.append(f"{name}: {exc}")
            continue
        manifest_path = skill_dir / "export-manifest.json"
        manifest = _read_json(manifest_path)
        diagnostics.extend(_schema_diagnostics(manifest, schema, str(manifest_path)))
        if not isinstance(manifest, dict):
            continue
        portable = manifest.get("portable")
        source = manifest.get("source")
        if not isinstance(portable, dict) or not isinstance(source, dict):
            continue
        expected_manifest_path = f"dist/agent-skills/{name}/export-manifest.json"
        bindings = {
            "source_skill_id": source.get("skill_id"),
            "portable_name": portable.get("name"),
            "source_artifact_digest": source.get("artifact_digest"),
            "portable_artifact_digest": portable.get("artifact_digest"),
            "lifecycle_status": source.get("lifecycle_status"),
            "production_routable": source.get("production_routable"),
            "export_manifest": expected_manifest_path,
        }
        for field, expected in bindings.items():
            if row.get(field) != expected:
                diagnostics.append(
                    f"{name}: registry binding mismatch for {field}:"
                    f" {row.get(field)!r} != {expected!r}"
                )
        if portable.get("directory") != f"dist/agent-skills/{name}":
            diagnostics.append(f"{name}: portable directory binding is stale")
        source_id_value = source.get("skill_id")
        if isinstance(source_id_value, str) and source.get("directory") != (
            f"skills/{source_id_value}"
        ):
            diagnostics.append(f"{name}: source directory binding is stale")
        digest, entries = _portable_digest_and_entries(skill_dir)
        if portable.get("artifact_digest") != digest:
            diagnostics.append(f"{name}: portable artifact digest is stale")
        if portable.get("files") != entries:
            diagnostics.append(f"{name}: portable file list is stale")
        metadata = frontmatter.get("metadata")
        if isinstance(metadata, dict):
            if metadata.get("ed3c-source-artifact-digest") != source.get(
                "artifact_digest"
            ):
                diagnostics.append(f"{name}: frontmatter source digest is stale")
            if metadata.get("ed3c-lifecycle-status") != source.get(
                "lifecycle_status"
            ):
                diagnostics.append(f"{name}: frontmatter lifecycle is stale")
            routable = str(source.get("production_routable")).lower()
            if metadata.get("ed3c-production-routable") != routable:
                diagnostics.append(f"{name}: frontmatter routability is stale")
        source_id = source.get("skill_id")
        if isinstance(source_id, str):
            source_skill = root.parents[1] / "skills" / source_id
            # This relative lookup is only used when validating a repository root
            # layout. Fresh generated temp trees are checked by byte comparison.
            if source_skill.exists():
                expected_source_digest = source_artifact_digest(source_skill)
                if source.get("artifact_digest") != expected_source_digest:
                    diagnostics.append(f"{name}: source artifact digest is stale")

    expected_registry_digest = sha256_bytes(canonical_bytes({"skills": rows}))
    if registry.get("registry_digest") != expected_registry_digest:
        diagnostics.append("registry.json: registry_digest is stale")
    root_entries = list(root.iterdir())
    if any(path.is_symlink() for path in root_entries):
        diagnostics.append("export root contains a symlink")
    actual_dirs = sorted(
        path.name for path in root_entries if path.is_dir() and not path.is_symlink()
    )
    if actual_dirs != sorted(seen_names):
        diagnostics.append(
            f"registry/export directory mismatch: registry={sorted(seen_names)}"
            f" directories={actual_dirs}"
        )
    root_files = sorted(
        path.name for path in root_entries if path.is_file() and not path.is_symlink()
    )
    if root_files != ["registry.json"]:
        diagnostics.append(
            f"unexpected export-root files: expected=['registry.json'] actual={root_files}"
        )
    if diagnostics:
        raise AgentSkillsExportError("; ".join(diagnostics))


def write_exports(
    *,
    source_root: Path | str,
    destination_root: Path | str,
    policy_path: Path | str,
) -> list[ExportedSkill]:
    destination = Path(destination_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="agent-skills-export-", dir=destination.parent
    ) as temporary:
        generated = Path(temporary) / "agent-skills"
        exported = generate_export_tree(source_root, generated, policy_path)
        backup = destination.with_name(destination.name + ".previous")
        if backup.exists():
            shutil.rmtree(backup)
        if destination.exists():
            os.replace(destination, backup)
        try:
            os.replace(generated, destination)
        except BaseException:
            if backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        finally:
            if backup.exists():
                shutil.rmtree(backup)
        return exported


def check_exports(
    *,
    source_root: Path | str,
    committed_root: Path | str,
    policy_path: Path | str,
    schema_path: Path | str,
) -> None:
    committed = Path(committed_root)
    with tempfile.TemporaryDirectory(prefix="agent-skills-check-") as temporary:
        generated = Path(temporary) / "agent-skills"
        generate_export_tree(source_root, generated, policy_path)
        diagnostics = compare_export_trees(generated, committed)
    if diagnostics:
        raise AgentSkillsExportError("; ".join(diagnostics))
    validate_export_manifests(committed, schema_path)
