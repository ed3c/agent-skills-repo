from __future__ import annotations

from pathlib import Path

from scripts.check_readme_index import validate_readme_index


ROOT = Path(__file__).resolve().parents[1]


def test_current_readme_index_is_complete() -> None:
    assert validate_readme_index(ROOT) == []


def test_missing_supported_entrypoint_is_rejected() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    target = "contracts/agent-skills-export.schema.json"
    changed = text.replace(f"]({target})", "](#removed-index-entry)", 1)

    failures = validate_readme_index(ROOT, readme_text=changed)

    assert f"missing README index entry: {target}" in failures


def test_broken_relative_link_is_rejected() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    changed = text + "\n[broken](does-not-exist/readme-target.md)\n"

    failures = validate_readme_index(ROOT, readme_text=changed)

    assert "README relative link target is absent: does-not-exist/readme-target.md" in failures


def test_repository_escape_link_is_rejected() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    changed = text + "\n[escape](../outside.md)\n"

    failures = validate_readme_index(ROOT, readme_text=changed)

    assert "README link escapes repository: ../outside.md" in failures
