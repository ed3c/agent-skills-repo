from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


Role = Literal["code", "document-data", "skill-composition"]
ParityStatus = Literal["equivalent", "known_loss", "rejected"]


class SkillsBenchAdapterError(ValueError):
    """An upstream task, import policy, bundle, or parity claim is invalid."""


@dataclass(frozen=True)
class UpstreamPolicy:
    repository: str
    commit: str
    license: str
    license_path: str
    benchflow_requirement: str


@dataclass(frozen=True)
class TaskSelection:
    task_id: str
    path: str
    role: Role
    expected_network_mode: str
    expected_skill_names: tuple[str, ...]
    required_files: tuple[str, ...]


@dataclass(frozen=True)
class ImportPolicy:
    upstream: UpstreamPolicy
    allowed_licenses: tuple[str, ...]
    tasks: tuple[TaskSelection, ...]


@dataclass(frozen=True)
class TaskDocument:
    raw_bytes: bytes
    frontmatter: dict[str, Any]
    prompt_bytes: bytes
    schema_version: str
    category: str
    subcategory: str | None
    modality: tuple[str, ...]
    interface: tuple[str, ...]
    skill_type: tuple[str, ...]
    network_mode: str


@dataclass(frozen=True)
class ImportedBundle:
    task_id: str
    role: Role
    bundle_digest: str
    bundle_dir: Path
    parity_status: ParityStatus
