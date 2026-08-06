"""Slice 2: repo_wiki_verified skill artifact, corpus, and admission dry-run.

Every admission evidence digest in this file comes from ACTUALLY RUNNING the
slice-1 anchor-oracle CLI (a temp git fixture per pool, so the hard fixture
HEAD gate is exercised) over the corpus's reference wiki pages. Nothing here
is asserted-by-hand: passed flags and evidence digests are read back from the
checker's verdict JSON.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from skill_arena.core import (
    CandidateRejected,
    CaseIndex,
    MutationCandidate,
    OracleEvidence,
    SkillManifest,
    admit_candidate,
    canonical_bytes,
    evaluate_hard_gates,
)
from skill_arena.skill_assets import (
    SkillAssetError,
    assert_corpus_exportable,
    compute_artifact_digest,
    domain_tokens,
    negative_case_vocabulary_conflicts,
)

PROJECT = Path(__file__).resolve().parents[1]
SKILL_DIR = PROJECT / "skills/repo_wiki_verified"
CORPUS_PATH = SKILL_DIR / "corpus.json"
MANIFEST_PATH = SKILL_DIR / "manifest.json"
FIXTURES = PROJECT / "tests/fixtures/repo_wiki_verified"
PUBLIC_SUBSET = FIXTURES / "public_fixture_subset"
PUBLIC_SUBSET_PROVENANCE = FIXTURES / "public_fixture_provenance.json"
BLIND_SEED = PROJECT / "tests/fixtures/blind_seed"
BLIND_CASES_PATH = BLIND_SEED / "blind_cases.json"

# Blind-pool material is DO-NOT-PUBLISH: public checkouts ship without it.
# Blind-dependent tests skip explicitly there; full coverage runs where the
# blind fixture exists.
BLIND_AVAILABLE = BLIND_CASES_PATH.is_file()
requires_blind = pytest.mark.skipif(
    not BLIND_AVAILABLE,
    reason="blind-pool material is DO-NOT-PUBLISH; absent from this checkout",
)
CLI = PROJECT / "scripts/anchor_oracle.py"
LINTER = PROJECT / "scripts/skill_description_linter.py"

ALLOWED_MUTATION_LABELS = {
    "boundary",
    "semantic_noise",
    "constraint_conflict",
    "chain_escalation",
}
PUBLIC_SEED_BANK = "qa/bank-public.json"
PINNED_PUBLIC_FIXTURE_SHA = "59a1f214fae1ccd06cc18aa8e923f3263d353c1d"
QUALIFICATION_SENTINEL = "pending-qualification"


@pytest.fixture(scope="module")
def corpus() -> dict:
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def blind_corpus() -> dict:
    # Blind gold cases live OUTSIDE the exportable corpus.json, next to the
    # DO-NOT-PUBLISH fixture they are seeded from.
    if not BLIND_AVAILABLE:
        pytest.skip(
            "blind-pool material is DO-NOT-PUBLISH; absent from this checkout"
        )
    return json.loads(BLIND_CASES_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def blind_cases_or_empty() -> list[dict]:
    # For tests that cover BOTH pools when blind material is present but must
    # still exercise the public pool on a public checkout without it.
    if not BLIND_AVAILABLE:
        return []
    return json.loads(BLIND_CASES_PATH.read_text(encoding="utf-8"))["cases"]


def cases_of(corpus: dict, pool: str) -> list[dict]:
    return [case for case in corpus["cases"] if case["pool"] == pool]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _run_checker_on_pool(
    cases: list[dict], fixture_source: Path, work: Path
) -> dict[str, dict]:
    """Run the slice-1 CLI over the pool's reference pages; map case_id->record."""
    repo = work / "fixture"
    shutil.copytree(fixture_source, repo)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.email=oracle@test",
        "-c",
        "user.name=oracle",
        "commit",
        "-qm",
        "pin fixture",
    )
    head = _git(repo, "rev-parse", "HEAD")

    wiki = work / "wiki"
    wiki.mkdir()
    for case in cases:
        (wiki / f"{case['case_id']}.md").write_text(
            case["reference_wiki_page"], encoding="utf-8"
        )

    output = work / "verdict.json"
    env = dict(os.environ, PYTHONPATH=str(PROJECT))
    result = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--wiki-dir",
            str(wiki),
            "--fixture-repo",
            str(repo),
            "--fixture-sha",
            head,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    verdict = json.loads(output.read_text(encoding="utf-8"))
    assert verdict["schema_version"] == "anchor-oracle-verdict@1"
    assert verdict["scope"] == "lexical-only"
    assert verdict["llm_judge_authority"] == "advisory_only"
    return {
        record["case_id"].removesuffix(".md"): record
        for record in verdict["cases"]
    }


@pytest.fixture(scope="module")
def checker_records(
    corpus: dict,
    blind_cases_or_empty: list[dict],
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, dict]:
    records: dict[str, dict] = {}
    records.update(
        _run_checker_on_pool(
            cases_of(corpus, "public"),
            PUBLIC_SUBSET,
            tmp_path_factory.mktemp("public_pool"),
        )
    )
    if blind_cases_or_empty:
        records.update(
            _run_checker_on_pool(
                blind_cases_or_empty,
                BLIND_SEED,
                tmp_path_factory.mktemp("blind_pool"),
            )
        )
    return records


def candidate_digest(case: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(case)).hexdigest()


def to_candidate(case: dict) -> MutationCandidate:
    return MutationCandidate(
        case_id=case["case_id"],
        seed_digest=case["seed"]["seed_digest"],
        candidate_digest=candidate_digest(case),
        candidate_payload=case,
        generator_kind="human",
        generator_build="slice2-corpus-authoring@1",
        mutation_labels=tuple(case["mutation_labels"]),
        requested_pool=case["pool"],
    )


def to_evidence(case: dict, record: dict) -> OracleEvidence:
    return OracleEvidence(
        evidence_id=f"anchor-oracle::{case['case_id']}",
        kind="test",
        verdict="pass" if record["passed"] else "fail",
        evidence_digest=record["evidence_digest"],
        independent=True,
    )


class TestSkillArtifact:
    def test_linter_accepts_all_skill_descriptions(self) -> None:
        result = subprocess.run(
            [sys.executable, str(LINTER)],
            capture_output=True,
            text=True,
            cwd=PROJECT,
        )
        assert result.returncode == 0, result.stderr + result.stdout

    def test_skills_md_has_required_sections(self) -> None:
        text = (SKILL_DIR / "skills.md").read_text(encoding="utf-8")
        for token in ("WHY:", "HOW:", "WHEN:", "WHEN NOT:"):
            assert token in text
        assert "references/" in text

    def test_manifest_covers_skill_manifest_fields(self, manifest: dict) -> None:
        required = {
            "skill_id",
            "artifact_digest",
            "capabilities",
            "non_capabilities",
            "permissions",
            "positive_exemplars",
            "negative_exemplars",
            "qualification_receipt_id",
        }
        assert required <= SkillManifest.__annotations__.keys()
        assert required <= manifest.keys()
        assert manifest["skill_id"] == "repo_wiki_verified"
        assert manifest["qualification_receipt_id"] == QUALIFICATION_SENTINEL

    def test_manifest_declares_explicit_non_capabilities(self, manifest: dict) -> None:
        text = " ".join(manifest["non_capabilities"]).lower()
        for concept in ("semantic", "network", "host qualification"):
            assert concept in text, f"non_capabilities never disclaims: {concept}"

    def test_artifact_digest_is_real_and_recomputable(self, manifest: dict) -> None:
        assert "artifact_digest_spec" in manifest, (
            "the canonicalization must be documented in the manifest itself"
        )
        recomputed = compute_artifact_digest(SKILL_DIR)
        assert manifest["artifact_digest"] == recomputed

    def test_artifact_digest_fails_closed_on_absent_artifact(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(SkillAssetError):
            compute_artifact_digest(tmp_path / "absent")


class TestVendoredPublicSubsetIntegrity:
    def test_vendored_bytes_match_pinned_provenance(self) -> None:
        provenance = json.loads(
            PUBLIC_SUBSET_PROVENANCE.read_text(encoding="utf-8")
        )
        assert provenance["pinned_sha"] == PINNED_PUBLIC_FIXTURE_SHA
        assert provenance["files"], "provenance lists no vendored files"
        for entry in provenance["files"]:
            target = PUBLIC_SUBSET / entry["path"]
            assert target.is_file(), f"vendored file missing: {entry['path']}"
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            assert digest == entry["sha256"], (
                f"vendored bytes drifted from pin: {entry['path']}"
            )

    def test_no_unlisted_files_in_subset(self) -> None:
        provenance = json.loads(
            PUBLIC_SUBSET_PROVENANCE.read_text(encoding="utf-8")
        )
        listed = {entry["path"] for entry in provenance["files"]}
        actual = {
            path.relative_to(PUBLIC_SUBSET).as_posix()
            for path in PUBLIC_SUBSET.rglob("*")
            if path.is_file()
        }
        assert actual == listed


class TestCorpusStructure:
    def test_all_three_hard_gate_groups_are_non_empty(self, corpus: dict) -> None:
        groups = {case["group"] for case in corpus["cases"]}
        assert groups == {"critical", "anchor", "target"}
        for group in ("critical", "anchor", "target"):
            count = sum(1 for case in corpus["cases"] if case["group"] == group)
            assert count >= 1, f"hard-gate group is empty: {group}"

    def test_mutation_labels_come_only_from_the_allowed_vocabulary(
        self, corpus: dict
    ) -> None:
        for case in corpus["cases"]:
            labels = set(case["mutation_labels"])
            assert labels, f"case has no mutation labels: {case['case_id']}"
            assert labels <= ALLOWED_MUTATION_LABELS, (
                f"unknown mutation labels on {case['case_id']}: "
                f"{labels - ALLOWED_MUTATION_LABELS}"
            )

    def test_every_bank_derived_case_is_pool_public(self, corpus: dict) -> None:
        # Both qa banks of the anchoring experiment are published, so any case
        # seeded from a bank can only ever live in the public pool.
        for case in corpus["cases"]:
            if "bank" in case["seed"]:
                assert case["pool"] == "public", (
                    f"bank-derived case must be public: {case['case_id']}"
                )
                assert case["seed"]["bank"] == PUBLIC_SEED_BANK

    def test_public_cases_record_bank_provenance(self, corpus: dict) -> None:
        public = cases_of(corpus, "public")
        assert public
        for case in public:
            assert case["seed"]["bank"] == PUBLIC_SEED_BANK
            assert case["seed"]["question_id"]

    def test_blind_pool_has_at_least_two_fresh_cases(
        self, corpus: dict, blind_corpus: dict
    ) -> None:
        blind = blind_corpus["cases"]
        assert len(blind) >= 2
        public_seeds = {
            case["seed"]["seed_digest"] for case in cases_of(corpus, "public")
        }
        for case in blind:
            assert case["pool"] == "blind"
            assert "bank" not in case["seed"]
            assert case["seed"]["fixture"] == "tests/fixtures/blind_seed"
            assert case["seed"]["seed_digest"] not in public_seeds

    @requires_blind
    def test_blind_seed_fixture_is_marked_do_not_publish(self) -> None:
        readme = (BLIND_SEED / "README.md").read_text(encoding="utf-8")
        assert "DO-NOT-PUBLISH" in readme

    def test_case_ids_are_unique(
        self, corpus: dict, blind_cases_or_empty: list[dict]
    ) -> None:
        ids = [
            case["case_id"]
            for case in corpus["cases"] + blind_cases_or_empty
        ]
        assert len(ids) == len(set(ids))

    def test_draft_threshold_is_declared_as_integer_ppm(self, corpus: dict) -> None:
        gate = corpus["hard_gate"]
        assert type(gate["target_success_threshold_ppm"]) is int
        assert 1 <= gate["target_success_threshold_ppm"] <= 1_000_000
        assert gate["threshold_status"] == "DRAFT"


class TestNegativeCaseGuard:
    def test_no_case_forbids_its_own_prompt_vocabulary(
        self, corpus: dict, blind_cases_or_empty: list[dict]
    ) -> None:
        # The gemini_interactions quarantine defect class, checked
        # mechanically instead of asserted — over both pools.
        assert (
            negative_case_vocabulary_conflicts(
                corpus["cases"] + blind_cases_or_empty
            )
            == []
        )

    def test_guard_exists_because_corpus_carries_forbidden_patterns(
        self, corpus: dict
    ) -> None:
        assert any(case.get("forbidden_patterns") for case in corpus["cases"]), (
            "guard would be vacuous: no case declares forbidden_patterns"
        )

    def test_guard_detects_a_planted_conflict(self) -> None:
        planted = [
            {
                "case_id": "planted-conflict",
                "prompt": "Record the proven order of the gates.",
                "forbidden_patterns": ["proven beyond dispute"],
            }
        ]
        conflicts = negative_case_vocabulary_conflicts(planted)
        assert len(conflicts) == 1
        assert conflicts[0]["case_id"] == "planted-conflict"
        assert "proven" in conflicts[0]["shared_tokens"]

    def test_guard_detects_whole_pattern_substring(self) -> None:
        planted = [
            {
                "case_id": "planted-substring",
                "prompt": "Say it is proven beyond dispute.",
                "forbidden_patterns": ["proven beyond dispute"],
            }
        ]
        conflicts = negative_case_vocabulary_conflicts(planted)
        assert conflicts and conflicts[0]["substring_hit"] is True

    def test_guard_fails_closed_on_missing_prompt(self) -> None:
        with pytest.raises(SkillAssetError):
            negative_case_vocabulary_conflicts(
                [{"case_id": "no-prompt", "forbidden_patterns": ["anything"]}]
            )

    def test_domain_tokens_drop_short_noise(self) -> None:
        assert domain_tokens("a an of the gate") == frozenset({"gate", "the"})


class TestAdmissionDryRun:
    def test_reference_pages_actually_pass_the_checker(
        self,
        corpus: dict,
        blind_cases_or_empty: list[dict],
        checker_records: dict[str, dict],
    ) -> None:
        for case in corpus["cases"] + blind_cases_or_empty:
            record = checker_records[case["case_id"]]
            assert record["passed"] is True, (
                f"reference page failed the anchor oracle: {case['case_id']}: "
                f"{record['failures']}"
            )
            assert record["evidence_digest"].startswith("sha256:")

    def test_every_public_case_admits_with_checker_evidence(
        self, corpus: dict, checker_records: dict[str, dict]
    ) -> None:
        index = CaseIndex()
        for case in cases_of(corpus, "public"):
            receipt = admit_candidate(
                to_candidate(case),
                [to_evidence(case, checker_records[case["case_id"]])],
                index,
            )
            assert receipt.state == "admitted"
            assert receipt.pool == "public"
            assert receipt.export_allowed is True

    def test_blind_cases_admit_into_the_blind_pool(
        self, blind_corpus: dict, checker_records: dict[str, dict]
    ) -> None:
        index = CaseIndex()
        for case in blind_corpus["cases"]:
            receipt = admit_candidate(
                to_candidate(case),
                [to_evidence(case, checker_records[case["case_id"]])],
                index,
            )
            assert receipt.state == "admitted"
            assert receipt.pool == "blind"
            assert receipt.export_allowed is False

    def test_duplicate_case_id_is_rejected(
        self, corpus: dict, checker_records: dict[str, dict]
    ) -> None:
        index = CaseIndex()
        case = cases_of(corpus, "public")[0]
        evidence = [to_evidence(case, checker_records[case["case_id"]])]
        admit_candidate(to_candidate(case), evidence, index)
        with pytest.raises(CandidateRejected, match="duplicate or empty case_id"):
            admit_candidate(to_candidate(case), evidence, index)

    def test_forged_candidate_digest_is_rejected(
        self, corpus: dict, checker_records: dict[str, dict]
    ) -> None:
        index = CaseIndex()
        case = cases_of(corpus, "public")[0]
        forged = MutationCandidate(
            case_id=case["case_id"],
            seed_digest=case["seed"]["seed_digest"],
            candidate_digest="sha256:" + "0" * 64,
            candidate_payload=case,
            generator_kind="human",
            generator_build="slice2-corpus-authoring@1",
            mutation_labels=tuple(case["mutation_labels"]),
            requested_pool=case["pool"],
        )
        with pytest.raises(CandidateRejected, match="digest mismatch"):
            admit_candidate(
                forged,
                [to_evidence(case, checker_records[case["case_id"]])],
                index,
            )

    def test_public_provenance_cannot_enter_the_blind_pool(
        self, corpus: dict, checker_records: dict[str, dict]
    ) -> None:
        index = CaseIndex()
        for case in cases_of(corpus, "public"):
            admit_candidate(
                to_candidate(case),
                [to_evidence(case, checker_records[case["case_id"]])],
                index,
            )
        public_case = cases_of(corpus, "public")[0]
        laundered = dict(public_case, case_id="laundered-into-blind", pool="blind")
        smuggled = MutationCandidate(
            case_id="laundered-into-blind",
            seed_digest=public_case["seed"]["seed_digest"],
            candidate_digest=candidate_digest(laundered),
            candidate_payload=laundered,
            generator_kind="human",
            generator_build="slice2-corpus-authoring@1",
            mutation_labels=tuple(public_case["mutation_labels"]),
            requested_pool="blind",
        )
        with pytest.raises(CandidateRejected, match="public provenance"):
            admit_candidate(
                smuggled,
                [to_evidence(public_case, checker_records[public_case["case_id"]])],
                index,
            )


class TestHardGateDryRun:
    def test_checker_derived_rows_pass_the_hard_gates(
        self,
        corpus: dict,
        blind_cases_or_empty: list[dict],
        checker_records: dict[str, dict],
    ) -> None:
        rows = [
            {
                "case_id": case["case_id"],
                "group": case["group"],
                "passed": checker_records[case["case_id"]]["passed"],
                "evidence_digest": checker_records[case["case_id"]][
                    "evidence_digest"
                ],
            }
            for case in corpus["cases"] + blind_cases_or_empty
        ]
        threshold = corpus["hard_gate"]["target_success_threshold_ppm"]
        result = evaluate_hard_gates(
            rows, target_success_threshold_ppm=threshold
        )
        assert result["failed_gates"] == []
        assert result["promotion_allowed"] is True
        assert result["target_success_rate_ppm"] >= threshold
        assert result["llm_judge_authority"] == "advisory_only"

    def test_hard_gate_fails_closed_when_a_critical_row_fails(
        self,
        corpus: dict,
        blind_cases_or_empty: list[dict],
        checker_records: dict[str, dict],
    ) -> None:
        # Positive control for the dry-run itself: flipping one checker-derived
        # critical verdict must fail the gate, proving the gate reads the rows.
        rows = [
            {
                "case_id": case["case_id"],
                "group": case["group"],
                "passed": checker_records[case["case_id"]]["passed"],
                "evidence_digest": checker_records[case["case_id"]][
                    "evidence_digest"
                ],
            }
            for case in corpus["cases"] + blind_cases_or_empty
        ]
        critical_row = next(row for row in rows if row["group"] == "critical")
        critical_row["passed"] = False
        result = evaluate_hard_gates(
            rows,
            target_success_threshold_ppm=corpus["hard_gate"][
                "target_success_threshold_ppm"
            ],
        )
        assert "critical_failure" in result["failed_gates"]
        assert result["promotion_allowed"] is False


class TestExportGuard:
    """corpus.json is the exportable file; blind gold must never live in it."""

    def test_corpus_file_contains_only_public_cases(self, corpus: dict) -> None:
        assert corpus["cases"]
        assert all(case["pool"] == "public" for case in corpus["cases"])

    def test_corpus_points_at_external_blind_cases_file(
        self, corpus: dict
    ) -> None:
        blind_pool = corpus["pools"]["blind"]
        assert (
            blind_pool["cases_file"]
            == "tests/fixtures/blind_seed/blind_cases.json"
        )
        if BLIND_AVAILABLE:
            assert BLIND_CASES_PATH.is_file()

    @requires_blind
    def test_no_blind_material_in_exportable_corpus(self, corpus: dict) -> None:
        assert_corpus_exportable(
            corpus, CORPUS_PATH.read_text(encoding="utf-8"), BLIND_SEED
        )

    @requires_blind
    def test_planted_blind_leak_is_caught(self, corpus: dict) -> None:
        # Pick the longest seed content line as the planted gold; naming the
        # seed file or its topic here would itself leak blind material into
        # published test source.
        gold = max(
            (
                line.strip()
                for path in sorted(BLIND_SEED.glob("*/*"))
                if path.is_file()
                for line in path.read_text(encoding="utf-8").splitlines()
            ),
            key=len,
        )
        leaked = CORPUS_PATH.read_text(encoding="utf-8") + gold
        with pytest.raises(SkillAssetError, match="blind material"):
            assert_corpus_exportable(corpus, leaked, BLIND_SEED)

    @requires_blind
    def test_non_public_case_in_corpus_is_caught(self, corpus: dict) -> None:
        polluted = dict(
            corpus,
            cases=corpus["cases"] + [{"case_id": "smuggled", "pool": "blind"}],
        )
        with pytest.raises(SkillAssetError, match="non-public"):
            assert_corpus_exportable(
                polluted, CORPUS_PATH.read_text(encoding="utf-8"), BLIND_SEED
            )

    def test_blind_seed_digests_recompute_from_repo(
        self, blind_corpus: dict
    ) -> None:
        # The blind seed digests must be recomputable from repo bytes alone,
        # per the seed_digest_spec recorded in blind_cases.json.
        for case in blind_corpus["cases"]:
            files = [
                {
                    "path": path,
                    "sha256": hashlib.sha256(
                        (BLIND_SEED / path).read_bytes()
                    ).hexdigest(),
                }
                for path in sorted(case["source_files"])
            ]
            expected = (
                "sha256:"
                + hashlib.sha256(
                    canonical_bytes(
                        {
                            "files": files,
                            "fixture": "tests/fixtures/blind_seed",
                        }
                    )
                ).hexdigest()
            )
            assert case["seed"]["seed_digest"] == expected

    def test_benchmark_corpus_digest_matches_raw_bytes(
        self, manifest: dict
    ) -> None:
        digest = (
            "sha256:" + hashlib.sha256(CORPUS_PATH.read_bytes()).hexdigest()
        )
        assert manifest["benchmark_corpus_digest"] == digest
        assert "raw bytes" in manifest["benchmark_corpus_digest_spec"]
