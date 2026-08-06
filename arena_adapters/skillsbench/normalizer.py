from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, cast

from .common import canonical_bytes, read_json_object, sha256_bytes
from .models import ImportPolicy, ImportedBundle, SkillsBenchAdapterError, TaskSelection
from .parity import report_digest, structural_parity_report
from .task import (
    discover_task_files,
    parse_task_md,
    task_skill_names,
    verify_upstream_checkout,
)

BUNDLE_SCHEMA_VERSION = "arena-task-bundle@1"
INDEX_SCHEMA_VERSION = "arena-task-bundle-index@1"
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
TASK_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
INDEX_ROLES = frozenset({"code", "document-data", "skill-composition"})
PARITY_STATES = frozenset({"equivalent", "known_loss", "rejected"})


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _copy_package(source: Path, destination: Path, files: list[dict[str, object]]) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for entry in files:
        relative = cast(str, entry["path"])
        source_path = source / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source_path.read_bytes())
        target.chmod(0o755 if entry["git_mode"] == "100755" else 0o644)


def _validate_selection(
    task_dir: Path,
    selection: TaskSelection,
) -> tuple[dict[str, Any], tuple[str, ...], list[dict[str, object]]]:
    task = parse_task_md(task_dir / "task.md")
    if task.network_mode != selection.expected_network_mode:
        raise SkillsBenchAdapterError(
            f"{selection.task_id}: network policy drift:"
            f" {task.network_mode!r} != {selection.expected_network_mode!r}"
        )
    skill_names = task_skill_names(task_dir)
    if skill_names != selection.expected_skill_names:
        raise SkillsBenchAdapterError(
            f"{selection.task_id}: skill injection drift:"
            f" {skill_names!r} != {selection.expected_skill_names!r}"
        )
    files = discover_task_files(task_dir)
    paths = {cast(str, item["path"]) for item in files}
    missing = sorted(set(selection.required_files) - paths)
    if missing:
        raise SkillsBenchAdapterError(
            f"{selection.task_id}: required package files are absent: {missing}"
        )
    summary = {
        "schema_version": task.schema_version,
        "category": task.category,
        "subcategory": task.subcategory,
        "modality": list(task.modality),
        "interface": list(task.interface),
        "skill_type": list(task.skill_type),
        "network_mode": task.network_mode,
        "task_md_sha256": sha256_bytes(task.raw_bytes),
        "prompt_sha256": sha256_bytes(task.prompt_bytes),
    }
    return summary, skill_names, files


def import_task(
    *,
    upstream_root: Path,
    output_root: Path,
    policy: ImportPolicy,
    selection: TaskSelection,
    license_digest: str,
) -> ImportedBundle:
    task_dir = upstream_root / selection.path
    try:
        task_dir.resolve(strict=True).relative_to(upstream_root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise SkillsBenchAdapterError(
            f"selected task escapes or is absent: {selection.path}"
        ) from exc
    task_summary, skill_names, files = _validate_selection(task_dir, selection)
    identity: dict[str, object] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "upstream": {
            "repository": policy.upstream.repository,
            "commit": policy.upstream.commit,
            "license": policy.upstream.license,
            "license_path": policy.upstream.license_path,
            "license_file_sha256": license_digest,
            "task_path": selection.path,
        },
        "task": {
            "task_id": selection.task_id,
            "role": selection.role,
            **task_summary,
        },
        "skill_injection": {
            "root": "environment/skills",
            "skill_names": list(skill_names),
            "baseline_contract": "no selected task skill is exposed to agent discovery",
            "candidate_contract": "only the declared selected skill set is exposed",
        },
        "files": files,
    }
    bundle_digest = sha256_bytes(canonical_bytes(identity))
    digest_hex = bundle_digest.removeprefix("sha256:")
    bundle_dir = output_root / selection.task_id / f"sha256-{digest_hex}"
    package_dir = bundle_dir / "package"
    _copy_package(task_dir, package_dir, files)
    manifest = {**identity, "bundle_digest": bundle_digest}
    _write_json(bundle_dir / "bundle.json", manifest)
    report = structural_parity_report(
        task_id=selection.task_id,
        bundle_digest=bundle_digest,
        upstream_package=task_dir,
        normalized_package=package_dir,
    )
    _write_json(bundle_dir / "parity.json", report)
    return ImportedBundle(
        task_id=selection.task_id,
        role=selection.role,
        bundle_digest=bundle_digest,
        bundle_dir=bundle_dir,
        parity_status=cast(Any, report["status"]),
    )


def import_selected_tasks(
    *,
    upstream_root: Path | str,
    output_root: Path | str,
    policy: ImportPolicy,
    verify_git: bool = True,
) -> list[ImportedBundle]:
    upstream = Path(upstream_root)
    destination = Path(output_root)
    if destination.exists():
        raise SkillsBenchAdapterError(f"output root must not exist: {destination}")
    license_digest = verify_upstream_checkout(upstream, policy, verify_git=verify_git)
    destination.parent.mkdir(parents=True, exist_ok=True)
    relative_bundles: list[tuple[ImportedBundle, Path]] = []
    with tempfile.TemporaryDirectory(
        prefix="skillsbench-import-", dir=destination.parent
    ) as temporary:
        generated = Path(temporary) / destination.name
        generated.mkdir()
        bundles = [
            import_task(
                upstream_root=upstream,
                output_root=generated,
                policy=policy,
                selection=selection,
                license_digest=license_digest,
            )
            for selection in policy.tasks
        ]
        relative_bundles = [
            (bundle, bundle.bundle_dir.relative_to(generated)) for bundle in bundles
        ]
        rows = [
            {
                "task_id": bundle.task_id,
                "role": bundle.role,
                "bundle_digest": bundle.bundle_digest,
                "bundle_path": relative.as_posix(),
                "parity_status": bundle.parity_status,
                "ranking_eligible": False,
            }
            for bundle, relative in relative_bundles
        ]
        index: dict[str, object] = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "upstream_repository": policy.upstream.repository,
            "upstream_commit": policy.upstream.commit,
            "bundles": rows,
        }
        index["index_digest"] = sha256_bytes(canonical_bytes(index))
        _write_json(generated / "index.json", index)
        os.replace(generated, destination)
    return [
        ImportedBundle(
            task_id=bundle.task_id,
            role=bundle.role,
            bundle_digest=bundle.bundle_digest,
            bundle_dir=destination / relative,
            parity_status=bundle.parity_status,
        )
        for bundle, relative in relative_bundles
    ]


def validate_bundle_directory(bundle_dir: Path | str) -> None:
    root = Path(bundle_dir)
    if root.is_symlink() or not root.is_dir():
        raise SkillsBenchAdapterError(f"bundle directory is absent: {root}")
    manifest_path = root / "bundle.json"
    parity_path = root / "parity.json"
    package = root / "package"
    if (
        manifest_path.is_symlink()
        or parity_path.is_symlink()
        or package.is_symlink()
        or not manifest_path.is_file()
        or not parity_path.is_file()
        or not package.is_dir()
    ):
        raise SkillsBenchAdapterError(f"bundle directory is incomplete: {root}")
    manifest = read_json_object(manifest_path)
    claimed = manifest.get("bundle_digest")
    identity = {key: value for key, value in manifest.items() if key != "bundle_digest"}
    expected = sha256_bytes(canonical_bytes(identity))
    if claimed != expected:
        raise SkillsBenchAdapterError("bundle_digest mismatch")
    if not isinstance(claimed, str) or not SHA256_RE.fullmatch(claimed):
        raise SkillsBenchAdapterError("bundle_digest is malformed")
    expected_directory = f"sha256-{claimed.removeprefix('sha256:')}"
    if root.name != expected_directory:
        raise SkillsBenchAdapterError(
            f"bundle directory name does not match digest: {root.name!r}"
        )
    task = manifest.get("task")
    if not isinstance(task, dict) or task.get("task_id") != root.parent.name:
        raise SkillsBenchAdapterError("bundle task identity does not match its path")
    expected_files = manifest.get("files")
    actual_files = discover_task_files(package)
    if expected_files != actual_files:
        raise SkillsBenchAdapterError("bundle package bytes or modes have drifted")
    parity = read_json_object(parity_path)
    if parity.get("bundle_digest") != claimed:
        raise SkillsBenchAdapterError("parity report is bound to another bundle")
    if parity.get("task_id") != task.get("task_id"):
        raise SkillsBenchAdapterError("parity report is bound to another task")
    if parity.get("report_digest") != report_digest(parity):
        raise SkillsBenchAdapterError("parity report digest mismatch")


def validate_bundle_index(output_root: Path | str) -> list[dict[str, object]]:
    """Validate index digest, row ordering, uniqueness, and bundle bindings."""
    root = Path(output_root)
    if root.is_symlink() or not root.is_dir():
        raise SkillsBenchAdapterError(f"bundle output root is absent: {root}")
    index_path = root / "index.json"
    if index_path.is_symlink() or not index_path.is_file():
        raise SkillsBenchAdapterError("bundle index is absent or is a symlink")
    index = read_json_object(index_path)
    expected_top = {
        "schema_version",
        "upstream_repository",
        "upstream_commit",
        "bundles",
        "index_digest",
    }
    if set(index) != expected_top:
        raise SkillsBenchAdapterError(
            f"bundle index fields differ: expected={sorted(expected_top)}"
            f" actual={sorted(index)}"
        )
    if index.get("schema_version") != INDEX_SCHEMA_VERSION:
        raise SkillsBenchAdapterError("bundle index schema_version is unsupported")
    repository = index.get("upstream_repository")
    commit = index.get("upstream_commit")
    if not isinstance(repository, str) or "/" not in repository:
        raise SkillsBenchAdapterError("bundle index upstream repository is invalid")
    if not isinstance(commit, str) or not FULL_SHA_RE.fullmatch(commit):
        raise SkillsBenchAdapterError("bundle index upstream commit is not a full SHA")
    claimed_digest = index.get("index_digest")
    expected_digest = sha256_bytes(
        canonical_bytes({key: value for key, value in index.items() if key != "index_digest"})
    )
    if claimed_digest != expected_digest:
        raise SkillsBenchAdapterError("bundle index digest mismatch")

    raw_rows = index.get("bundles")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise SkillsBenchAdapterError("bundle index has no rows")
    rows: list[dict[str, object]] = []
    task_ids: list[str] = []
    digests: set[str] = set()
    paths: set[str] = set()
    expected_row_keys = {
        "task_id",
        "role",
        "bundle_digest",
        "bundle_path",
        "parity_status",
        "ranking_eligible",
    }
    for index_number, raw_row in enumerate(raw_rows):
        if not isinstance(raw_row, dict) or set(raw_row) != expected_row_keys:
            raise SkillsBenchAdapterError(
                f"bundle index row {index_number} has unsupported or missing fields"
            )
        row = cast(dict[str, object], raw_row)
        task_id = row.get("task_id")
        role = row.get("role")
        digest = row.get("bundle_digest")
        bundle_path = row.get("bundle_path")
        parity_status = row.get("parity_status")
        ranking_eligible = row.get("ranking_eligible")
        if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
            raise SkillsBenchAdapterError(f"bundle index row {index_number} task_id is invalid")
        if role not in INDEX_ROLES:
            raise SkillsBenchAdapterError(f"bundle index row {index_number} role is invalid")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise SkillsBenchAdapterError(f"bundle index row {index_number} digest is invalid")
        if not isinstance(bundle_path, str):
            raise SkillsBenchAdapterError(f"bundle index row {index_number} path is invalid")
        path = Path(bundle_path)
        expected_path = f"{task_id}/sha256-{digest.removeprefix('sha256:')}"
        if (
            path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != bundle_path
            or bundle_path != expected_path
        ):
            raise SkillsBenchAdapterError(
                f"bundle index row {index_number} path is not canonical: {bundle_path!r}"
            )
        if parity_status not in PARITY_STATES:
            raise SkillsBenchAdapterError(
                f"bundle index row {index_number} parity status is invalid"
            )
        if not isinstance(ranking_eligible, bool):
            raise SkillsBenchAdapterError(
                f"bundle index row {index_number} ranking_eligible is not boolean"
            )
        if ranking_eligible != (parity_status == "equivalent"):
            raise SkillsBenchAdapterError(
                f"bundle index row {index_number} eligibility contradicts parity status"
            )
        if task_id in task_ids or digest in digests or bundle_path in paths:
            raise SkillsBenchAdapterError("bundle index contains duplicate identity fields")
        task_ids.append(task_id)
        digests.add(digest)
        paths.add(bundle_path)

        bundle_dir = root / bundle_path
        validate_bundle_directory(bundle_dir)
        manifest = read_json_object(bundle_dir / "bundle.json")
        parity = read_json_object(bundle_dir / "parity.json")
        task = manifest.get("task")
        if not isinstance(task, dict):
            raise SkillsBenchAdapterError(f"bundle {task_id} has no task identity")
        bindings = {
            "task_id": task.get("task_id"),
            "role": task.get("role"),
            "bundle_digest": manifest.get("bundle_digest"),
            "parity_status": parity.get("status"),
            "ranking_eligible": parity.get("ranking_eligible"),
        }
        for field, expected in bindings.items():
            if row.get(field) != expected:
                raise SkillsBenchAdapterError(
                    f"bundle index row {task_id} binding mismatch for {field}:"
                    f" {row.get(field)!r} != {expected!r}"
                )
        upstream = manifest.get("upstream")
        if not isinstance(upstream, dict):
            raise SkillsBenchAdapterError(f"bundle {task_id} has no upstream identity")
        if upstream.get("repository") != repository or upstream.get("commit") != commit:
            raise SkillsBenchAdapterError(
                f"bundle {task_id} upstream identity differs from index"
            )
        rows.append(row)

    if task_ids != sorted(task_ids):
        raise SkillsBenchAdapterError("bundle index rows must be sorted by task_id")
    actual_dirs = sorted(
        path.name for path in root.iterdir() if path.is_dir() and not path.is_symlink()
    )
    if actual_dirs != task_ids:
        raise SkillsBenchAdapterError(
            f"bundle index task directories differ: index={task_ids} actual={actual_dirs}"
        )
    root_files = sorted(
        path.name for path in root.iterdir() if path.is_file() and not path.is_symlink()
    )
    if root_files != ["index.json"]:
        raise SkillsBenchAdapterError(
            f"unexpected bundle-root files: expected=['index.json'] actual={root_files}"
        )
    if any(path.is_symlink() for path in root.iterdir()):
        raise SkillsBenchAdapterError("bundle output root contains a symlink")
    return rows
