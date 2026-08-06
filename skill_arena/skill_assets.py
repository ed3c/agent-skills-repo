"""Deterministic skill-asset helpers: artifact digests and corpus guards.

``compute_artifact_digest`` defines the canonical byte representation of a
skill artifact (skills.md plus every regular file under references/), so a
manifest's ``artifact_digest`` is recomputable by anyone from the artifact
files alone.

``negative_case_vocabulary_conflicts`` is the mechanical guard against the
gemini_interactions quarantine defect class: a negative case whose forbidden
patterns overlap the domain vocabulary its own prompt requires would confound
correct non-activation with refusal to perform the task. The guard is lexical
and deterministic; it never claims semantic judgment.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Mapping, Sequence

from skill_arena.core import canonical_bytes

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_MINIMUM_TOKEN_LENGTH = 3


class SkillAssetError(ValueError):
    """A skill asset is absent or malformed: fail closed, never guess."""


def compute_artifact_digest(skill_dir: Path | str) -> str:
    """Digest a skill artifact's files: skills.md + references/**, recursively.

    Canonicalization (documented verbatim in the skill manifest): entries are
    ``{"path": <POSIX relpath>, "sha256": <hex of file bytes>}`` sorted by
    path; the canonical value is ``{"files": [...]}`` serialized with
    ``skill_arena.core.canonical_bytes``; the digest is
    ``"sha256:" + sha256(canonical bytes)``. ``manifest.json`` is excluded
    because the manifest cannot digest itself; ``corpus.json`` is excluded
    because the corpus is benchmark data, not skill behavior.
    """
    skill = Path(skill_dir)
    skill_md = skill / "skills.md"
    references = skill / "references"
    if not skill_md.is_file():
        raise SkillAssetError(f"skill artifact is missing skills.md: {skill}")
    if not references.is_dir():
        raise SkillAssetError(f"skill artifact is missing references/: {skill}")
    files = [
        skill_md,
        *(path for path in references.rglob("*") if path.is_file()),
    ]
    entries = sorted(
        (
            {
                "path": path.relative_to(skill).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in files
        ),
        key=lambda entry: entry["path"],
    )
    return (
        "sha256:"
        + hashlib.sha256(canonical_bytes({"files": entries})).hexdigest()
    )


def assert_corpus_exportable(
    corpus: Mapping[str, object],
    corpus_text: str,
    blind_fixture_dir: Path | str,
    *,
    exclude_names: frozenset[str] = frozenset(
        {"README.md", "blind_cases.json"}
    ),
    minimum_line_length: int = 12,
) -> None:
    """Fail closed unless the exportable corpus file is free of blind material.

    Two mechanical checks: every case in the exportable corpus must be pool
    ``"public"``, and no content line (stripped, at least
    ``minimum_line_length`` chars) from the blind fixture's seed files may
    occur in the corpus text. The fixture's README and case bank are excluded
    from the scan — they share structural lines with any corpus file — while
    gold leakage always reproduces a seed content line, which the seed files
    themselves catch.
    """
    cases = corpus.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SkillAssetError("corpus has no cases list")
    for case in cases:
        if not isinstance(case, Mapping) or case.get("pool") != "public":
            case_id = case.get("case_id") if isinstance(case, Mapping) else case
            raise SkillAssetError(
                f"non-public case in exportable corpus: {case_id!r}"
            )
    blind_dir = Path(blind_fixture_dir)
    if not blind_dir.is_dir():
        raise SkillAssetError(
            f"blind fixture directory missing: {blind_dir}"
        )
    seed_files = [
        path
        for path in sorted(blind_dir.rglob("*"))
        if path.is_file() and path.name not in exclude_names
    ]
    if not seed_files:
        raise SkillAssetError(f"blind fixture has no seed files: {blind_dir}")
    for path in seed_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SkillAssetError(
                f"blind seed file unreadable: {path}: {exc}"
            ) from exc
        for line in text.splitlines():
            stripped = line.strip()
            if len(stripped) >= minimum_line_length and stripped in corpus_text:
                raise SkillAssetError(
                    "blind material leaked into exportable corpus:"
                    f" {path.relative_to(blind_dir)}: {stripped[:60]!r}"
                )


def domain_tokens(text: str) -> frozenset[str]:
    """Lowercase alphanumeric tokens of length >= 3 (shorter is noise)."""
    return frozenset(
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) >= _MINIMUM_TOKEN_LENGTH
    )


def negative_case_vocabulary_conflicts(
    cases: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Report every case whose forbidden patterns collide with its own prompt.

    A conflict is a forbidden pattern that shares a domain token with the
    case's prompt, or appears whole (case-insensitive) inside the prompt.
    A case with forbidden patterns but no prompt is its own explicit failure
    state, never a silent pass.
    """
    conflicts: list[dict[str, object]] = []
    for case in cases:
        patterns = case.get("forbidden_patterns") or []
        if not patterns:
            continue
        prompt = case.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise SkillAssetError(
                "case declares forbidden_patterns but has no prompt:"
                f" {case.get('case_id')!r}"
            )
        prompt_tokens = domain_tokens(prompt)
        for pattern in patterns:
            pattern_text = str(pattern)
            shared = sorted(domain_tokens(pattern_text) & prompt_tokens)
            substring_hit = pattern_text.lower() in prompt.lower()
            if shared or substring_hit:
                conflicts.append(
                    {
                        "case_id": case.get("case_id"),
                        "pattern": pattern_text,
                        "shared_tokens": shared,
                        "substring_hit": substring_hit,
                    }
                )
    return conflicts
