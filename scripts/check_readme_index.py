#!/usr/bin/env python3
"""Validate README navigation and supported-entrypoint completeness."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(
    os.environ.get("README_INDEX_ROOT", Path(__file__).resolve().parents[1])
).resolve()

_LINK_RE = re.compile(
    r"\[[^\]]*\]\((?P<target><[^>]+>|[^)\s]+)(?:\s+['\"][^)]*['\"])?\)"
)

_ROOT_AUTHORITY_PATHS = {
    ".plan-package.lock.yaml",
    "AGENTS.md",
    "CLAUDE.md",
    "INTEGRATION_REQUIREMENTS.md",
    "LICENSE",
    "PROJECT-SSOT.md",
    "README-EXPERIMENT.md",
    "plan-package.compat.yaml",
    "pyproject.toml",
    "requirements.lock",
}

_OPENWIKI_PATHS = {
    "openwiki/index.md",
    "openwiki/quickstart.md",
    "openwiki/qualification-pipeline.md",
    "openwiki/anchor-oracle-comparison.md",
    "openwiki/architecture",
    "openwiki/governance",
    "openwiki/lifecycle",
    "openwiki/nonofficial",
    "openwiki/operations",
    "openwiki/skill-assets",
    "openwiki/terminal-operator",
    "openwiki/testing",
    "openwiki/validation",
}

_OPERATOR_PATHS = {
    ".agents/skills/repo-terminal-operator",
    ".agents/skills/repo-terminal-operator/SKILL.md",
    ".githooks",
    "artifacts/repo-terminal-operator",
    "tests/fixtures",
}

_PACKAGE_DIRECTORIES = (
    "anchor_oracle",
    "arena_adapters",
    "arena_adapters/skillsbench",
    "skill_arena",
    "skill_arena/experiment",
    "skill_arena/sandbox_executor",
)


def _repo_relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _direct_files(root: Path, relative: str, pattern: str) -> set[str]:
    directory = root / relative
    if not directory.is_dir():
        return {relative}
    return {
        _repo_relative(path, root)
        for path in directory.glob(pattern)
        if path.is_file() and not path.is_symlink()
    }


def _recursive_files(root: Path, relative: str, pattern: str) -> set[str]:
    directory = root / relative
    if not directory.is_dir():
        return {relative}
    return {
        _repo_relative(path, root)
        for path in directory.rglob(pattern)
        if path.is_file() and not path.is_symlink()
    }


def _direct_children(root: Path, relative: str) -> set[str]:
    directory = root / relative
    if not directory.is_dir():
        return {relative}
    return {
        _repo_relative(path, root)
        for path in directory.iterdir()
        if not path.is_symlink() and (path.is_dir() or path.is_file())
    }


def collect_expected_paths(root: Path = ROOT) -> set[str]:
    """Return every supported README entrypoint derived from repository bytes."""

    root = root.resolve()
    expected = set(_ROOT_AUTHORITY_PATHS)
    expected.update(_OPENWIKI_PATHS)
    expected.update(_OPERATOR_PATHS)

    expected.update(_direct_files(root, "contracts", "*.json"))
    expected.update(_direct_files(root, "scripts", "*"))
    expected.update(_direct_files(root, ".github/workflows", "*.y*ml"))
    expected.update(_recursive_files(root, "docs", "*.md"))
    expected.update(_direct_files(root, "tests", "test_*.py"))

    for relative in _PACKAGE_DIRECTORIES:
        expected.update(_direct_files(root, relative, "*.py"))

    expected.update(_direct_children(root, "skills"))
    expected.update(_direct_children(root, "dist/agent-skills"))
    expected.update(_direct_children(root, "data"))
    return expected


def _normalize_relative_target(target: str) -> str | None:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    if target.startswith("#"):
        return None

    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    path = unquote(parsed.path)
    if not path:
        return None
    return Path(path).as_posix().rstrip("/")


def collect_readme_links(
    readme_text: str,
    *,
    root: Path = ROOT,
) -> tuple[set[str], list[str]]:
    """Collect safe repository-relative links and broken-link diagnostics."""

    root = root.resolve()
    links: set[str] = set()
    failures: list[str] = []
    for match in _LINK_RE.finditer(readme_text):
        raw_target = match.group("target")
        normalized = _normalize_relative_target(raw_target)
        if normalized is None:
            continue
        relative = Path(normalized)
        if relative.is_absolute() or ".." in relative.parts:
            failures.append(f"README link escapes repository: {raw_target}")
            continue
        candidate = (root / relative).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            failures.append(f"README link resolves outside repository: {raw_target}")
            continue
        if not candidate.exists():
            failures.append(f"README relative link target is absent: {normalized}")
            continue
        links.add(normalized)
    return links, failures


def validate_readme_index(
    root: Path = ROOT,
    *,
    readme_text: str | None = None,
) -> list[str]:
    """Validate relative links and complete supported-entrypoint coverage."""

    root = root.resolve()
    readme = root / "README.md"
    if readme_text is None:
        try:
            readme_text = readme.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return [f"cannot read README.md: {exc}"]

    links, failures = collect_readme_links(readme_text, root=root)
    expected = collect_expected_paths(root)
    for missing in sorted(expected - links):
        failures.append(f"missing README index entry: {missing}")
    return failures


def main() -> int:
    failures = validate_readme_index(ROOT)
    if failures:
        print("FAIL: README index validation failed", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 2
    print(
        "PASS: README links and supported-entrypoint index are complete "
        f"({len(collect_expected_paths(ROOT))} entries)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
