from __future__ import annotations

import re
import stat
import subprocess
from pathlib import Path
from typing import Any, cast

import yaml

from .common import sha256_bytes
from .models import ImportPolicy, SkillsBenchAdapterError, TaskDocument

TASK_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_TASK_TOP_LEVEL = frozenset(
    {"schema_version", "metadata", "verifier", "agent", "sandbox", "oracle"}
)
REQUIRED_TASK_TOP_LEVEL = frozenset(
    {"schema_version", "metadata", "verifier", "agent", "sandbox"}
)
MAX_FILE_COUNT = 10_000
MAX_TOTAL_BYTES = 1_000_000_000


def parse_task_md(path: Path | str) -> TaskDocument:
    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise SkillsBenchAdapterError(f"cannot read task.md: {source}: {exc}") from exc
    if not raw.startswith(b"---\n"):
        raise SkillsBenchAdapterError(f"task.md must begin with YAML frontmatter: {source}")
    closing = raw.find(b"\n---\n", 4)
    if closing < 0:
        raise SkillsBenchAdapterError(f"task.md frontmatter is not closed: {source}")
    try:
        frontmatter = yaml.safe_load(raw[4:closing].decode("utf-8"))
        raw.decode("utf-8")
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise SkillsBenchAdapterError(f"task.md is not valid UTF-8/YAML: {source}: {exc}") from exc
    if not isinstance(frontmatter, dict):
        raise SkillsBenchAdapterError("task.md frontmatter must be a mapping")
    unexpected = sorted(set(frontmatter) - ALLOWED_TASK_TOP_LEVEL)
    missing = sorted(REQUIRED_TASK_TOP_LEVEL - set(frontmatter))
    if unexpected:
        raise SkillsBenchAdapterError(f"task.md has unsupported top-level fields: {unexpected}")
    if missing:
        raise SkillsBenchAdapterError(f"task.md is missing top-level fields: {missing}")
    if frontmatter.get("schema_version") != "1.3":
        raise SkillsBenchAdapterError(
            f"unsupported task schema_version: {frontmatter.get('schema_version')!r}"
        )
    metadata = frontmatter.get("metadata")
    verifier = frontmatter.get("verifier")
    agent = frontmatter.get("agent")
    sandbox = frontmatter.get("sandbox")
    if not all(isinstance(value, dict) for value in (metadata, verifier, agent, sandbox)):
        raise SkillsBenchAdapterError("metadata, verifier, agent, and sandbox must be mappings")
    metadata = cast(dict[str, Any], metadata)
    verifier = cast(dict[str, Any], verifier)
    sandbox = cast(dict[str, Any], sandbox)
    if verifier.get("type") != "test-script":
        raise SkillsBenchAdapterError("only deterministic test-script verifiers are admitted")
    category = metadata.get("category")
    if not isinstance(category, str) or not category:
        raise SkillsBenchAdapterError("metadata.category must be a non-empty string")
    subcategory = metadata.get("subcategory")
    if subcategory is not None and not isinstance(subcategory, str):
        raise SkillsBenchAdapterError("metadata.subcategory must be a string when present")

    def string_list(field: str) -> tuple[str, ...]:
        value = metadata.get(field)
        if not isinstance(value, list) or not value or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise SkillsBenchAdapterError(f"metadata.{field} must be a non-empty string list")
        return tuple(cast(list[str], value))

    network = sandbox.get("network_mode")
    if network not in {"public", "no-network"}:
        raise SkillsBenchAdapterError(f"unsupported sandbox.network_mode: {network!r}")
    prompt = raw[closing + len(b"\n---\n") :]
    if not prompt.strip():
        raise SkillsBenchAdapterError("task prompt is empty")
    return TaskDocument(
        raw_bytes=raw,
        frontmatter=cast(dict[str, Any], frontmatter),
        prompt_bytes=prompt,
        schema_version="1.3",
        category=category,
        subcategory=cast(str | None, subcategory),
        modality=string_list("modality"),
        interface=string_list("interface"),
        skill_type=string_list("skill_type"),
        network_mode=cast(str, network),
    )


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=False
    )


def verify_upstream_checkout(
    root: Path | str,
    policy: ImportPolicy,
    *,
    verify_git: bool = True,
) -> str:
    checkout = Path(root)
    if checkout.is_symlink() or not checkout.is_dir():
        raise SkillsBenchAdapterError(f"upstream checkout is absent: {checkout}")
    license_file = checkout / policy.upstream.license_path
    if license_file.is_symlink() or not license_file.is_file():
        raise SkillsBenchAdapterError(
            f"upstream license file is absent: {policy.upstream.license_path}"
        )
    license_digest = sha256_bytes(license_file.read_bytes())
    if not verify_git:
        return license_digest
    head = _run_git(checkout, "rev-parse", "HEAD")
    if head.returncode != 0 or head.stdout.strip() != policy.upstream.commit:
        raise SkillsBenchAdapterError(
            f"upstream checkout HEAD does not match policy commit:"
            f" {head.stdout.strip()!r} != {policy.upstream.commit!r}"
        )
    status_result = _run_git(checkout, "status", "--porcelain", "--untracked-files=all")
    if status_result.returncode != 0:
        raise SkillsBenchAdapterError("cannot inspect upstream checkout cleanliness")
    if status_result.stdout:
        raise SkillsBenchAdapterError("upstream checkout must be clean before import")
    return license_digest


def discover_task_files(task_dir: Path | str) -> list[dict[str, object]]:
    root = Path(task_dir)
    if root.is_symlink() or not root.is_dir():
        raise SkillsBenchAdapterError(f"task package is absent: {root}")
    entries: list[dict[str, object]] = []
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise SkillsBenchAdapterError(f"task package contains a symlink: {relative}")
        if path.is_dir():
            continue
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise SkillsBenchAdapterError(f"task package contains a special file: {relative}")
        data = path.read_bytes()
        total_bytes += len(data)
        entries.append(
            {
                "path": relative,
                "sha256": sha256_bytes(data),
                "size_bytes": len(data),
                "git_mode": "100755" if mode & 0o111 else "100644",
            }
        )
        if len(entries) > MAX_FILE_COUNT or total_bytes > MAX_TOTAL_BYTES:
            raise SkillsBenchAdapterError("task package exceeds import safety limits")
    if not entries:
        raise SkillsBenchAdapterError("task package contains no files")
    return entries


def task_skill_names(task_dir: Path | str) -> tuple[str, ...]:
    root = Path(task_dir) / "environment" / "skills"
    if not root.exists():
        return ()
    if root.is_symlink() or not root.is_dir():
        raise SkillsBenchAdapterError("environment/skills must be a real directory")
    names: list[str] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.is_symlink() or not child.is_dir():
            raise SkillsBenchAdapterError(
                f"skill injection root contains a non-directory entry: {child.name}"
            )
        if not TASK_ID_RE.fullmatch(child.name):
            raise SkillsBenchAdapterError(f"task-local skill name is invalid: {child.name!r}")
        skill_md = child / "SKILL.md"
        if skill_md.is_symlink() or not skill_md.is_file():
            raise SkillsBenchAdapterError(
                f"task-local skill lacks regular SKILL.md: {child.name}"
            )
        names.append(child.name)
    return tuple(names)
