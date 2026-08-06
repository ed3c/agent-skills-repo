from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, cast

from .models import ImportPolicy, ImportedBundle, SkillsBenchAdapterError, TaskSelection
from .common import canonical_bytes, sha256_bytes
from .task import (
    discover_task_files,
    parse_task_md,
    task_skill_names,
    verify_upstream_checkout,
)
from .parity import report_digest, structural_parity_report

BUNDLE_SCHEMA_VERSION = "arena-task-bundle@1"
INDEX_SCHEMA_VERSION = "arena-task-bundle-index@1"


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
        rows = [
            {
                "task_id": bundle.task_id,
                "role": bundle.role,
                "bundle_digest": bundle.bundle_digest,
                "bundle_path": bundle.bundle_dir.relative_to(generated).as_posix(),
                "parity_status": bundle.parity_status,
                "ranking_eligible": False,
            }
            for bundle in bundles
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
            bundle_dir=destination / bundle.bundle_dir.relative_to(generated),
            parity_status=bundle.parity_status,
        )
        for bundle in bundles
    ]


def validate_bundle_directory(bundle_dir: Path | str) -> None:
    root = Path(bundle_dir)
    manifest_path = root / "bundle.json"
    parity_path = root / "parity.json"
    package = root / "package"
    if not manifest_path.is_file() or not parity_path.is_file() or not package.is_dir():
        raise SkillsBenchAdapterError(f"bundle directory is incomplete: {root}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise SkillsBenchAdapterError("bundle.json must be an object")
    claimed = manifest.get("bundle_digest")
    identity = {key: value for key, value in manifest.items() if key != "bundle_digest"}
    expected = sha256_bytes(canonical_bytes(identity))
    if claimed != expected:
        raise SkillsBenchAdapterError("bundle_digest mismatch")
    expected_files = manifest.get("files")
    actual_files = discover_task_files(package)
    if expected_files != actual_files:
        raise SkillsBenchAdapterError("bundle package bytes or modes have drifted")
    parity = json.loads(parity_path.read_text(encoding="utf-8"))
    if not isinstance(parity, dict):
        raise SkillsBenchAdapterError("parity.json must be an object")
    if parity.get("bundle_digest") != claimed:
        raise SkillsBenchAdapterError("parity report is bound to another bundle")
    if parity.get("report_digest") != report_digest(parity):
        raise SkillsBenchAdapterError("parity report digest mismatch")
