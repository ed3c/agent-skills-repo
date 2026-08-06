from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import SkillsBenchAdapterError


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def read_json_object(path: Path | str) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillsBenchAdapterError(f"cannot read JSON object {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise SkillsBenchAdapterError(f"JSON root must be an object: {source}")
    return value


def safe_repository_path(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SkillsBenchAdapterError(f"{label} must be a non-empty path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise SkillsBenchAdapterError(
            f"{label} must be a normalized relative POSIX path: {value!r}"
        )
    return value
