from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from skill_arena.agent_skills_export import (
    AgentSkillsExportError,
    check_exports,
    compare_export_trees,
    discover_source_skills,
    generate_export_tree,
    validate_export_manifests,
    validate_portable_skill,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "data/agent-skills/export-policy.json"
SCHEMA = ROOT / "contracts/agent-skills-export.schema.json"


def write_skill(
    root: Path,
    skill_id: str,
    *,
    body: str | None = None,
    status: dict[str, object] | None = None,
) -> Path:
    skill = root / skill_id
    skill.mkdir(parents=True)
    text = body or (
        f"# {skill_id}\n\n"
        "WHY: Perform a reusable operation.\n\n"
        "HOW: Follow the bundled procedure.\n\n"
        "WHEN: A task requires the reusable operation.\n\n"
        "WHEN NOT: The task is unrelated.\n"
    )
    (skill / "skills.md").write_text(text, encoding="utf-8")
    (skill / "references").mkdir()
    (skill / "references/reference.md").write_bytes(b"reference bytes\n")
    if status is not None:
        (skill / "status.json").write_text(
            json.dumps(status, indent=2) + "\n", encoding="utf-8"
        )
    return skill


def split_body(skill_md: bytes) -> bytes:
    closing = skill_md.find(b"\n---\n", 4)
    assert closing > 0
    return skill_md[closing + len(b"\n---\n") :]


def test_current_repository_skills_export_and_validate(tmp_path: Path) -> None:
    output = tmp_path / "agent-skills"
    exported = generate_export_tree(ROOT / "skills", output, POLICY)
    assert [skill.portable_name for skill in exported] == [
        "autoresearch-composer",
        "gemini-interactions",
        "repo-wiki-verified",
    ]
    validate_export_manifests(output, SCHEMA)
    for skill in exported:
        validate_portable_skill(output / skill.portable_name)

    repo_manifest = json.loads(
        (output / "repo-wiki-verified/export-manifest.json").read_text()
    )
    assert repo_manifest["source"]["artifact_digest"] == (
        "sha256:8e3f18ed0623c7b3fb6b22e7fc3b1884dcc27582477a72c918a5fde833d9d5e6"
    )


def test_body_and_behavior_resources_are_byte_preserving(tmp_path: Path) -> None:
    output = tmp_path / "agent-skills"
    generate_export_tree(ROOT / "skills", output, POLICY)
    pairs = [
        ("autoresearch_composer", "autoresearch-composer", "state_graph.md"),
        ("gemini_interactions", "gemini-interactions", "deploy_guide.md"),
        ("repo_wiki_verified", "repo-wiki-verified", "procedure.md"),
    ]
    for source_id, portable, reference in pairs:
        source = ROOT / "skills" / source_id
        exported = output / portable
        assert split_body((exported / "SKILL.md").read_bytes()) == (
            source / "skills.md"
        ).read_bytes()
        assert (exported / "references" / reference).read_bytes() == (
            source / "references" / reference
        ).read_bytes()
        manifest = json.loads((exported / "export-manifest.json").read_text())
        assert manifest["losses"] == []
        transformations = {
            item["field"]: item["kind"] for item in manifest["transformations"]
        }
        assert transformations["SKILL.md-body"] == "byte-preserving"
        assert transformations["behavior-resources"] == "byte-preserving"
    for source_id, portable in [
        ("autoresearch_composer", "autoresearch-composer"),
        ("gemini_interactions", "gemini-interactions"),
    ]:
        assert (output / portable / "cases.json").read_bytes() == (
            ROOT / "skills" / source_id / "cases.json"
        ).read_bytes()


def test_conformance_does_not_change_lifecycle_or_routability(tmp_path: Path) -> None:
    output = tmp_path / "agent-skills"
    generate_export_tree(ROOT / "skills", output, POLICY)
    registry = json.loads((output / "registry.json").read_text())
    rows = {row["portable_name"]: row for row in registry["skills"]}
    assert rows["autoresearch-composer"]["lifecycle_status"] == (
        "production-seed-candidate"
    )
    assert rows["autoresearch-composer"]["production_routable"] is False
    assert rows["gemini-interactions"]["lifecycle_status"] == "quarantined"
    assert rows["gemini-interactions"]["production_routable"] is False
    assert rows["repo-wiki-verified"]["lifecycle_status"] == "pending-qualification"
    assert rows["repo-wiki-verified"]["production_routable"] is False

    autoresearch_frontmatter = validate_portable_skill(
        output / "autoresearch-composer"
    )
    assert autoresearch_frontmatter["metadata"]["ed3c-lifecycle-status"] == (
        "production-seed-candidate"
    )
    assert (
        autoresearch_frontmatter["metadata"]["ed3c-production-routable"]
        == "false"
    )

    autoresearch_manifest = json.loads(
        (output / "autoresearch-composer/export-manifest.json").read_text()
    )
    assert autoresearch_manifest["source"]["lifecycle_source"] == (
        "data/lifecycle/skill_optimization_registry.json"
        "+data/lifecycle/promotion_records.json"
    )

    gemini_frontmatter = validate_portable_skill(output / "gemini-interactions")
    assert gemini_frontmatter["metadata"]["ed3c-lifecycle-status"] == "quarantined"
    assert gemini_frontmatter["metadata"]["ed3c-production-routable"] == "false"


def test_export_is_reproducible_across_directories(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    generate_export_tree(ROOT / "skills", first, POLICY)
    generate_export_tree(ROOT / "skills", second, POLICY)
    assert compare_export_trees(first, second) == []


def test_check_detects_stale_skill_and_digest(tmp_path: Path) -> None:
    committed = tmp_path / "committed"
    generate_export_tree(ROOT / "skills", committed, POLICY)
    skill_md = committed / "gemini-interactions/SKILL.md"
    skill_md.write_bytes(skill_md.read_bytes() + b"\ntampered\n")
    with pytest.raises(AgentSkillsExportError, match="stale export file"):
        check_exports(
            source_root=ROOT / "skills",
            committed_root=committed,
            policy_path=POLICY,
            schema_path=SCHEMA,
        )

    fresh = tmp_path / "fresh"
    generate_export_tree(ROOT / "skills", fresh, POLICY)
    manifest_path = fresh / "repo-wiki-verified/export-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["portable"]["artifact_digest"] = "sha256:" + "0" * 64
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    with pytest.raises(AgentSkillsExportError, match="portable artifact digest is stale"):
        validate_export_manifests(fresh, SCHEMA)


def test_name_collision_and_invalid_source_id_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    write_skill(root, "foo_bar")
    write_skill(root, "foo-bar")
    with pytest.raises(AgentSkillsExportError, match="portable name collision"):
        discover_source_skills(root)

    invalid = tmp_path / "invalid"
    invalid.mkdir()
    write_skill(invalid, "Bad_Name")
    with pytest.raises(AgentSkillsExportError, match="source skill id"):
        discover_source_skills(invalid)


def test_missing_or_reordered_sections_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    write_skill(
        root,
        "broken_skill",
        body=(
            "# Broken\n\nWHY: It claims a capability.\n\n"
            "WHEN: A task asks for it.\n\n"
            "HOW: This marker is out of order.\n\n"
            "WHEN NOT: Unrelated work.\n"
        ),
    )
    with pytest.raises(AgentSkillsExportError, match="exactly one ordered"):
        generate_export_tree(root, tmp_path / "output", POLICY)


def test_symlink_resource_escape_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    skill = write_skill(root, "safe_skill")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    (skill / "references/escape.md").symlink_to(outside)
    with pytest.raises(AgentSkillsExportError, match="symlink resources"):
        generate_export_tree(root, tmp_path / "output", POLICY)


def test_symlink_skill_directory_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "outside"
    source.mkdir()
    write_skill(source, "linked_skill")
    root = tmp_path / "skills"
    root.mkdir()
    (root / "linked_skill").symlink_to(source / "linked_skill", target_is_directory=True)
    with pytest.raises(AgentSkillsExportError, match="symlink entries"):
        discover_source_skills(root)


def test_status_claim_must_have_boolean_routability(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    write_skill(
        root,
        "bad_status",
        status={"status": "qualified", "production_routable": "yes"},
    )
    with pytest.raises(AgentSkillsExportError, match="boolean production_routable"):
        generate_export_tree(root, tmp_path / "output", POLICY)


def test_unexpected_export_file_and_registry_drift_are_rejected(tmp_path: Path) -> None:
    output = tmp_path / "agent-skills"
    generate_export_tree(ROOT / "skills", output, POLICY)
    (output / "untracked.txt").write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(AgentSkillsExportError, match="unexpected export-root files"):
        validate_export_manifests(output, SCHEMA)

    shutil.rmtree(output)
    generate_export_tree(ROOT / "skills", output, POLICY)
    registry_path = output / "registry.json"
    registry = json.loads(registry_path.read_text())
    registry["registry_digest"] = "sha256:" + "0" * 64
    registry_path.write_text(json.dumps(registry, indent=2) + "\n")
    with pytest.raises(AgentSkillsExportError, match="registry_digest is stale"):
        validate_export_manifests(output, SCHEMA)
