from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Mapping

import pytest

from arena_adapters.skillsbench.common import canonical_bytes, sha256_bytes
from arena_adapters.skillsbench.models import SkillsBenchAdapterError
from arena_adapters.skillsbench.normalizer import (
    import_selected_tasks,
    validate_bundle_directory,
    validate_bundle_index,
)
from arena_adapters.skillsbench.parity import bind_execution_parity
from arena_adapters.skillsbench.policy import load_policy


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


def write_task(root: Path, task_id: str, role: str, skills: list[str]) -> None:
    task = root / "tasks" / task_id
    (task / "environment" / "skills").mkdir(parents=True)
    (task / "oracle").mkdir()
    (task / "verifier").mkdir()
    body = f"""---
schema_version: '1.3'
metadata:
  author_name: Test
  author_email: test@example.invalid
  difficulty: easy
  category: software-engineering
  subcategory: adapter-test
  category_confidence: high
  task_type: [implementation]
  modality: [source-code]
  interface: [terminal, python]
  skill_type: [tool-workflow]
  tags: [test]
verifier:
  type: test-script
  timeout_sec: 30
agent:
  timeout_sec: 30
sandbox:
  network_mode: no-network
  cpus: 1
  memory_mb: 512
  storage_mb: 512
  gpus: 0
---

Produce /app/result.json for {role}.
"""
    (task / "task.md").write_text(body, encoding="utf-8")
    (task / "environment" / "Dockerfile").write_text("FROM python:3.12-slim\n")
    (task / "environment" / "input.bin").write_bytes(b"\x00\xffbinary\n")
    for skill in skills:
        skill_dir = task / "environment" / "skills" / skill
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: Test skill\n---\n\n# Test\n",
            encoding="utf-8",
        )
    for path in (task / "oracle" / "solve.sh", task / "verifier" / "test.sh"):
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)


def fixture_checkout(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "skillsbench"
    root.mkdir()
    (root / "LICENSE").write_text("Apache License 2.0\n", encoding="utf-8")
    write_task(root, "code-task", "code", ["code-skill"])
    write_task(root, "data-task", "document-data", ["data-skill"])
    write_task(
        root,
        "composition-task",
        "skill-composition",
        ["first-skill", "second-skill"],
    )
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "adapter@example.invalid")
    git(root, "config", "user.name", "Adapter Test")
    git(root, "add", ".")
    git(root, "commit", "-m", "fixture")
    return root, git(root, "rev-parse", "HEAD")


def write_policy(path: Path, commit: str) -> Path:
    value = {
        "schema_version": "skillsbench-import-policy@1",
        "upstream": {
            "repository": "benchflow-ai/skillsbench",
            "commit": commit,
            "license": "Apache-2.0",
            "license_path": "LICENSE",
            "benchflow_requirement": "benchflow>=0.6.3,<0.7",
        },
        "allowed_licenses": [
            "Apache-2.0",
            "BSD-2-Clause",
            "BSD-3-Clause",
            "ISC",
            "MIT",
        ],
        "tasks": [
            {
                "task_id": "code-task",
                "path": "tasks/code-task",
                "role": "code",
                "expected_network_mode": "no-network",
                "expected_skill_names": ["code-skill"],
                "required_files": [
                    "environment/Dockerfile",
                    "oracle/solve.sh",
                    "task.md",
                    "verifier/test.sh",
                ],
            },
            {
                "task_id": "composition-task",
                "path": "tasks/composition-task",
                "role": "skill-composition",
                "expected_network_mode": "no-network",
                "expected_skill_names": ["first-skill", "second-skill"],
                "required_files": [
                    "environment/Dockerfile",
                    "oracle/solve.sh",
                    "task.md",
                    "verifier/test.sh",
                ],
            },
            {
                "task_id": "data-task",
                "path": "tasks/data-task",
                "role": "document-data",
                "expected_network_mode": "no-network",
                "expected_skill_names": ["data-skill"],
                "required_files": [
                    "environment/Dockerfile",
                    "oracle/solve.sh",
                    "task.md",
                    "verifier/test.sh",
                ],
            },
        ],
    }
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return path


def test_imports_content_addressed_byte_preserving_bundles(tmp_path: Path) -> None:
    upstream, commit = fixture_checkout(tmp_path)
    policy = load_policy(write_policy(tmp_path / "policy.json", commit))
    output = tmp_path / "bundles"
    bundles = import_selected_tasks(
        upstream_root=upstream, output_root=output, policy=policy
    )
    assert [bundle.task_id for bundle in bundles] == [
        "code-task",
        "composition-task",
        "data-task",
    ]
    assert {bundle.parity_status for bundle in bundles} == {"known_loss"}
    rows = validate_bundle_index(output)
    assert [row["task_id"] for row in rows] == [
        "code-task",
        "composition-task",
        "data-task",
    ]
    for bundle in bundles:
        validate_bundle_directory(bundle.bundle_dir)
        assert bundle.bundle_dir.name == (
            "sha256-" + bundle.bundle_digest.removeprefix("sha256:")
        )
    imported_binary = next(bundle for bundle in bundles if bundle.task_id == "code-task")
    assert (
        imported_binary.bundle_dir / "package/environment/input.bin"
    ).read_bytes() == b"\x00\xffbinary\n"
    assert os.access(
        imported_binary.bundle_dir / "package/oracle/solve.sh", os.X_OK
    )


def test_mutable_or_wrong_commit_is_rejected(tmp_path: Path) -> None:
    upstream, commit = fixture_checkout(tmp_path)
    policy_path = write_policy(tmp_path / "policy.json", commit)
    raw = json.loads(policy_path.read_text())
    raw["upstream"]["commit"] = "main"
    policy_path.write_text(json.dumps(raw))
    with pytest.raises(SkillsBenchAdapterError, match="full lowercase SHA"):
        load_policy(policy_path)

    policy = load_policy(write_policy(policy_path, "f" * 40))
    with pytest.raises(SkillsBenchAdapterError, match="HEAD does not match"):
        import_selected_tasks(
            upstream_root=upstream,
            output_root=tmp_path / "bad-output",
            policy=policy,
        )


def test_unknown_task_field_fails_closed(tmp_path: Path) -> None:
    upstream, _ = fixture_checkout(tmp_path)
    task_md = upstream / "tasks/code-task/task.md"
    task_md.write_text(
        task_md.read_text().replace(
            "verifier:\n", "unknown_surface: true\nverifier:\n"
        )
    )
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "unknown field")
    commit = git(upstream, "rev-parse", "HEAD")
    policy = load_policy(write_policy(tmp_path / "policy.json", commit))
    with pytest.raises(SkillsBenchAdapterError, match="unsupported top-level fields"):
        import_selected_tasks(
            upstream_root=upstream,
            output_root=tmp_path / "output",
            policy=policy,
        )


def test_symlink_and_dirty_checkout_fail_closed(tmp_path: Path) -> None:
    upstream, commit = fixture_checkout(tmp_path)
    policy = load_policy(write_policy(tmp_path / "policy.json", commit))
    outside = tmp_path / "outside"
    outside.write_text("secret")
    (upstream / "tasks/code-task/environment/escape").symlink_to(outside)
    with pytest.raises(SkillsBenchAdapterError, match="clean"):
        import_selected_tasks(
            upstream_root=upstream,
            output_root=tmp_path / "dirty",
            policy=policy,
        )

    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "symlink")
    commit = git(upstream, "rev-parse", "HEAD")
    policy = load_policy(write_policy(tmp_path / "policy2.json", commit))
    with pytest.raises(SkillsBenchAdapterError, match="symlink"):
        import_selected_tasks(
            upstream_root=upstream,
            output_root=tmp_path / "symlink",
            policy=policy,
        )


def test_bundle_tamper_is_rejected(tmp_path: Path) -> None:
    upstream, commit = fixture_checkout(tmp_path)
    policy = load_policy(write_policy(tmp_path / "policy.json", commit))
    bundles = import_selected_tasks(
        upstream_root=upstream,
        output_root=tmp_path / "bundles",
        policy=policy,
    )
    bundle = bundles[0]
    (bundle.bundle_dir / "package/task.md").write_text("tampered\n")
    with pytest.raises(SkillsBenchAdapterError, match="drifted"):
        validate_bundle_directory(bundle.bundle_dir)


def test_index_digest_and_bundle_bindings_are_enforced(tmp_path: Path) -> None:
    upstream, commit = fixture_checkout(tmp_path)
    policy = load_policy(write_policy(tmp_path / "policy.json", commit))
    output = tmp_path / "bundles"
    import_selected_tasks(upstream_root=upstream, output_root=output, policy=policy)
    index_path = output / "index.json"
    index = json.loads(index_path.read_text())
    index["index_digest"] = "sha256:" + "0" * 64
    index_path.write_text(json.dumps(index))
    with pytest.raises(SkillsBenchAdapterError, match="index digest mismatch"):
        validate_bundle_index(output)

    shutil.rmtree(output)
    import_selected_tasks(upstream_root=upstream, output_root=output, policy=policy)
    index = json.loads(index_path.read_text())
    index["bundles"][0]["parity_status"] = "equivalent"
    index["bundles"][0]["ranking_eligible"] = True
    index["index_digest"] = sha256_bytes(
        canonical_bytes({key: value for key, value in index.items() if key != "index_digest"})
    )
    index_path.write_text(json.dumps(index))
    with pytest.raises(SkillsBenchAdapterError, match="binding mismatch"):
        validate_bundle_index(output)


def execution_evidence(
    report: Mapping[str, object],
    surface: str,
    probe_digest: str,
    *,
    reward: float = 1.0,
    diagnostics_class: str = "pass",
    diagnostics_digest: str | None = None,
    task_id: str | None = None,
    bundle_digest: str | None = None,
) -> dict[str, object]:
    result_fill = "c" if surface == "upstream" else "d"
    value: dict[str, object] = {
        "schema_version": "skillsbench-execution-evidence@1",
        "task_id": task_id or report["task_id"],
        "bundle_digest": bundle_digest or report["bundle_digest"],
        "surface": surface,
        "upstream": {
            "repository": "benchflow-ai/skillsbench",
            "commit": "1" * 40,
        },
        "execution": {
            "benchflow_version": "0.6.3",
            "agent": "oracle",
            "sandbox": "docker",
        },
        "task_check_passed": True,
        "oracle": {
            "result_digest": "sha256:" + result_fill * 64,
            "task_digest": "sha256:" + "e" * 64,
            "reward": reward,
            "error": None,
            "verifier_error": None,
        },
        "verifier_probe": {
            "input_digest": probe_digest,
            "reward": reward,
            "diagnostics_class": diagnostics_class,
            "diagnostics_digest": diagnostics_digest or ("sha256:" + "f" * 64),
        },
    }
    value["evidence_digest"] = sha256_bytes(canonical_bytes(value))
    return value


def test_execution_evidence_promotes_only_exact_bound_parity(tmp_path: Path) -> None:
    upstream, commit = fixture_checkout(tmp_path)
    policy = load_policy(write_policy(tmp_path / "policy.json", commit))
    bundle = import_selected_tasks(
        upstream_root=upstream,
        output_root=tmp_path / "bundles",
        policy=policy,
    )[0]
    report = json.loads((bundle.bundle_dir / "parity.json").read_text())
    probe = "sha256:" + "a" * 64
    equivalent = bind_execution_parity(
        report,
        execution_evidence(report, "upstream", probe),
        execution_evidence(report, "normalized", probe),
    )
    assert equivalent["status"] == "equivalent"
    assert equivalent["ranking_eligible"] is True
    assert equivalent["execution"]["same_oracle_task_digest"] is True
    assert equivalent["execution"]["diagnostics_digest_equal"] is True

    rejected = bind_execution_parity(
        report,
        execution_evidence(report, "upstream", probe),
        execution_evidence(
            report,
            "normalized",
            "sha256:" + "b" * 64,
        ),
    )
    assert rejected["status"] == "rejected"
    assert rejected["ranking_eligible"] is False


def test_execution_evidence_cannot_be_reused_or_tampered(tmp_path: Path) -> None:
    upstream, commit = fixture_checkout(tmp_path)
    policy = load_policy(write_policy(tmp_path / "policy.json", commit))
    bundle = import_selected_tasks(
        upstream_root=upstream,
        output_root=tmp_path / "bundles",
        policy=policy,
    )[0]
    report = json.loads((bundle.bundle_dir / "parity.json").read_text())
    probe = "sha256:" + "a" * 64

    wrong_task = execution_evidence(
        report,
        "upstream",
        probe,
        task_id="another-task",
    )
    with pytest.raises(SkillsBenchAdapterError, match="another task"):
        bind_execution_parity(
            report,
            wrong_task,
            execution_evidence(report, "normalized", probe),
        )

    tampered = execution_evidence(report, "upstream", probe)
    tampered["oracle"]["reward"] = 0.0  # type: ignore[index]
    with pytest.raises(SkillsBenchAdapterError, match="evidence_digest mismatch"):
        bind_execution_parity(
            report,
            tampered,
            execution_evidence(report, "normalized", probe),
        )

    wrong_bundle = execution_evidence(
        report,
        "normalized",
        probe,
        bundle_digest="sha256:" + "9" * 64,
    )
    with pytest.raises(SkillsBenchAdapterError, match="another bundle"):
        bind_execution_parity(
            report,
            execution_evidence(report, "upstream", probe),
            wrong_bundle,
        )


def test_license_and_role_coverage_are_enforced(tmp_path: Path) -> None:
    _, commit = fixture_checkout(tmp_path)
    path = write_policy(tmp_path / "policy.json", commit)
    raw = json.loads(path.read_text())
    raw["allowed_licenses"] = ["MIT"]
    path.write_text(json.dumps(raw))
    with pytest.raises(SkillsBenchAdapterError, match="not allowed"):
        load_policy(path)

    raw = json.loads(write_policy(path, commit).read_text())
    raw["tasks"] = raw["tasks"][:2]
    path.write_text(json.dumps(raw))
    with pytest.raises(SkillsBenchAdapterError, match="required Arena roles"):
        load_policy(path)
