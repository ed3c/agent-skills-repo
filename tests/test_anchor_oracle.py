from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from anchor_oracle import (
    CircularDenylistInvalid,
    EmptyWiki,
    FixtureDirty,
    FixtureHeadUnverifiable,
    FixtureMissing,
    FixtureShaMismatch,
    UnreadableInput,
    WikiMissing,
    canonical_bytes,
    check_anchor,
    evaluate_page,
    evaluate_wiki,
    extract_anchors,
    validate_okf_frontmatter,
    verify_fixture_head,
)

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = PROJECT / "tests/fixtures/anchor_oracle"
FIXTURE_REPO = FIXTURES / "fixture_repo"
WIKI_GOOD = FIXTURES / "wiki_good"
WIKI_HOLLOW = FIXTURES / "wiki_hollow"
CLI = PROJECT / "scripts/anchor_oracle.py"
FIXTURE_SHA = "0" * 40


def issue_codes(validation: dict) -> set[str]:
    return {issue["code"] for issue in validation["issues"]}


class TestDeclaredDependencies:
    """PyYAML is a runtime import; the declared resolution inputs must carry it."""

    def test_pyyaml_declared_in_pyproject(self) -> None:
        project = tomllib.loads((PROJECT / "pyproject.toml").read_text())
        deps = project["project"]["dependencies"]
        assert any(
            re.match(r"(?i)^pyyaml\s*==", dep) for dep in deps
        ), f"PyYAML missing from pyproject dependencies: {deps}"

    def test_pyyaml_pinned_in_requirements_lock(self) -> None:
        lock = (PROJECT / "requirements.lock").read_text()
        assert re.search(
            r"(?im)^pyyaml==\d", lock
        ), "PyYAML missing from requirements.lock"


class TestOkfFrontmatter:
    def test_valid_with_producer_extensions(self) -> None:
        content = (
            "---\n"
            "type: Quickstart\n"
            "title: agent-skills-repo wiki quickstart\n"
            "description: Entry point for the generated wiki.\n"
            "tags: [quickstart, navigation]\n"
            "node_kind: RepoDoc\n"
            "openwiki_generated: true\n"
            "generated_at: null\n"
            "---\n\n# Quickstart\n"
        )
        assert validate_okf_frontmatter(content) == {"valid": True, "issues": []}

    def test_crlf_delimiters_are_accepted(self) -> None:
        content = "---\r\ntype: Reference\r\n---\r\nbody\r\n"
        assert validate_okf_frontmatter(content)["valid"] is True

    def test_missing_opening_delimiter(self) -> None:
        result = validate_okf_frontmatter("# no front matter\n")
        assert result["valid"] is False
        assert issue_codes(result) == {"missing_opening_delimiter"}
        assert result["issues"][0]["line"] == 1

    def test_missing_closing_delimiter(self) -> None:
        result = validate_okf_frontmatter("---\ntype: Reference\n")
        assert issue_codes(result) == {"missing_closing_delimiter"}

    def test_invalid_yaml(self) -> None:
        result = validate_okf_frontmatter("---\ntype: [unclosed\n---\n")
        assert issue_codes(result) == {"invalid_yaml"}

    def test_duplicate_keys_are_invalid_yaml(self) -> None:
        result = validate_okf_frontmatter("---\ntype: A\ntype: B\n---\n")
        assert issue_codes(result) == {"invalid_yaml"}

    def test_non_mapping_root_is_invalid(self) -> None:
        result = validate_okf_frontmatter("---\n- a\n- b\n---\n")
        assert issue_codes(result) == {"invalid_yaml_root"}

    def test_empty_block_is_invalid_root(self) -> None:
        result = validate_okf_frontmatter("---\n\n---\n")
        assert issue_codes(result) == {"invalid_yaml_root"}

    def test_missing_type_is_reported(self) -> None:
        result = validate_okf_frontmatter("---\ntitle: T\n---\n")
        assert issue_codes(result) == {"missing_type"}

    def test_non_string_and_blank_okf_fields(self) -> None:
        result = validate_okf_frontmatter(
            "---\ntype: 42\ntitle: '   '\ndescription: [x]\n---\n"
        )
        assert issue_codes(result) == {
            "invalid_type",
            "invalid_title",
            "invalid_description",
        }

    def test_invalid_tags(self) -> None:
        result = validate_okf_frontmatter("---\ntype: R\ntags: nope\n---\n")
        assert issue_codes(result) == {"invalid_tags"}
        result = validate_okf_frontmatter("---\ntype: R\ntags: ['', a]\n---\n")
        assert issue_codes(result) == {"invalid_tags"}

    def test_yaml12_core_unquoted_timestamp_is_a_string(self) -> None:
        # The TS reference parses with schema:"core" (YAML 1.2): no timestamp
        # resolver, so an unquoted ISO date stays a string and is valid.
        result = validate_okf_frontmatter(
            "---\ntype: Reference\ntimestamp: 2026-08-06\n---\n"
        )
        assert result == {"valid": True, "issues": []}

    def test_yaml12_core_no_yaml11_booleans_or_sexagesimal(self) -> None:
        # YAML 1.2 core: yes/no/on/off are strings, 12:34:56 is a string.
        result = validate_okf_frontmatter(
            "---\ntype: No\ntitle: 12:34:56\ndescription: on\n---\n"
        )
        assert result == {"valid": True, "issues": []}

    def test_yaml12_core_scalars_still_resolve(self) -> None:
        # true/false, ints and null must still resolve as non-strings.
        result = validate_okf_frontmatter(
            "---\ntype: true\ntitle: 42\ndescription: null\nresource: 1.5\n---\n"
        )
        assert issue_codes(result) == {
            "invalid_type",
            "invalid_title",
            "invalid_description",
            "invalid_resource",
        }

    def test_modest_alias_use_is_accepted(self) -> None:
        result = validate_okf_frontmatter(
            "---\ntype: R\nx: &a shared\ny: *a\n---\n"
        )
        assert result == {"valid": True, "issues": []}

    def test_excessive_alias_expansion_is_invalid_yaml(self) -> None:
        # Mirrors the reference's maxAliasCount: 100 resource-exhaustion guard.
        content = (
            "---\ntype: R\n"
            "a: &a [x, x, x, x, x, x, x, x, x, x]\n"
            "b: [*a, *a, *a, *a, *a, *a, *a, *a, *a, *a]\n"
            "---\n"
        )
        result = validate_okf_frontmatter(content)
        assert issue_codes(result) == {"invalid_yaml"}


class TestAnchorExtraction:
    def test_extracts_path_and_quote(self) -> None:
        anchors, malformed = extract_anchors(
            "Threshold (src: src/demo.py `MAGIC_THRESHOLD = 42`)."
        )
        assert malformed == []
        assert len(anchors) == 1
        assert anchors[0].source_path == "src/demo.py"
        assert anchors[0].quote == "MAGIC_THRESHOLD = 42"
        assert anchors[0].line_ref is None

    def test_extracts_optional_line_refs(self) -> None:
        anchors, malformed = extract_anchors(
            "One (src: src/demo.py:7 `return`) two (src: a.py:3-9 `x`)."
        )
        assert malformed == []
        assert [a.source_path for a in anchors] == ["src/demo.py", "a.py"]
        assert anchors[0].line_ref == {"start": 7, "end": 7}
        assert anchors[1].line_ref == {"start": 3, "end": 9}

    def test_unterminated_quote_is_malformed_not_dropped(self) -> None:
        anchors, malformed = extract_anchors("Bad (src: src/demo.py `oops).")
        assert anchors == []
        assert len(malformed) == 1
        assert "malformed" in malformed[0]["reason"]

    def test_missing_opening_parenthesis_is_malformed(self) -> None:
        anchors, malformed = extract_anchors("Bad src: src/demo.py `quote` here.")
        assert anchors == []
        assert len(malformed) == 1
        assert "parenthesis" in malformed[0]["reason"]

    def test_src_token_inside_quote_is_not_malformed(self) -> None:
        # The quote grammar ([^`]+) legally contains "(src:" — e.g. a page
        # documenting the anchor grammar itself must not be flagged.
        anchors, malformed = extract_anchors(
            "Grammar (src: docs/grammar.md `anchors look like (src: path) refs`)."
        )
        assert malformed == []
        assert len(anchors) == 1
        assert anchors[0].quote == "anchors look like (src: path) refs"

    def test_src_token_in_quote_tail_not_missing_paren(self) -> None:
        # Round-1 bug class, MISSING_PAREN_RE flavor: a quote legally ending
        # with "src: <token> " must not be spliced with a later backtick (a
        # plain code span) into a phantom "missing opening parenthesis".
        anchors, malformed = extract_anchors(
            "Doc (src: doc.md `greps for src: path `) and later `code`."
        )
        assert malformed == []
        assert len(anchors) == 1
        assert anchors[0].quote == "greps for src: path "

    def test_src_token_in_quote_tail_before_second_anchor(self) -> None:
        # Same splice, but the later backtick belongs to a second anchor.
        anchors, malformed = extract_anchors(
            "A (src: a.md `greps for src: path `) B (src: b.md `x`)."
        )
        assert malformed == []
        assert [a.source_path for a in anchors] == ["a.md", "b.md"]

    def test_bare_attempt_after_anchor_with_src_tail_in_quote(self) -> None:
        # Round-3 regression: a MISSING_PAREN_RE match whose `src:` sits inside
        # a well-formed anchor's quote is rightly skipped, but the skipped
        # match must not consume the text it spans — that would silently
        # swallow a later genuine bare attempt starting inside that span.
        anchors, malformed = extract_anchors(
            "(src: a.md `has src: p `) then bare src: q `quote` end."
        )
        assert len(anchors) == 1
        assert len(malformed) == 1
        assert "opening parenthesis" in malformed[0]["reason"]

    def test_paren_in_quote_not_coreported_with_genuine_malformed(self) -> None:
        # A genuinely malformed anchor must not drag well-formed anchors whose
        # quotes contain ")" into the malformed report.
        anchors, malformed = extract_anchors(
            "Good (src: src/a.py `call(x))`) and bad (src: src/b.py `oops)."
        )
        assert len(anchors) == 1
        assert anchors[0].quote == "call(x))"
        assert len(malformed) == 1
        assert "src/b.py" in malformed[0]["fragment"]


class TestMalformedDeduplication:
    def test_inner_bare_attempt_inside_fragment_yields_one_entry(self) -> None:
        # One authoring mistake, one diagnostic: a malformed `(src:` fragment
        # whose interior happens to contain a bare-attempt-shaped region must
        # not be reported twice (once per scan).
        anchors, malformed = extract_anchors(
            "Bad (src: src/a.py src: src/b.py `quote`) end."
        )
        assert anchors == []
        assert len(malformed) == 1
        assert "malformed anchor" in malformed[0]["reason"]

    def test_bare_attempt_outside_fragment_still_reported(self) -> None:
        # Dedup must not weaken either scan: a genuine bare attempt outside
        # any malformed fragment's span keeps its own entry.
        anchors, malformed = extract_anchors(
            "Bad (src: broken) and bare src: b.py `quote` end."
        )
        assert anchors == []
        reasons = sorted(item["reason"] for item in malformed)
        assert len(malformed) == 2
        assert "opening parenthesis" in reasons[0]
        assert "malformed anchor" in reasons[1]


class TestFragmentExcerptTrimming:
    def test_bare_attempt_fragment_trims_stray_prefix_char(self) -> None:
        # The (?:^|[^(]) alternative consumes one char before `src:`; a
        # non-whitespace char (here `)`) must not leak into the excerpt.
        anchors, malformed = extract_anchors("call()src: src/b.py `quote` end.")
        assert anchors == []
        assert len(malformed) == 1
        assert malformed[0]["fragment"] == "src: src/b.py `quote`"

    def test_bare_attempt_fragment_trims_whitespace_prefix(self) -> None:
        anchors, malformed = extract_anchors("Bad src: src/b.py `quote` end.")
        assert len(malformed) == 1
        assert malformed[0]["fragment"] == "src: src/b.py `quote`"


def anchor_for(text: str):
    anchors, malformed = extract_anchors(text)
    assert malformed == [] and len(anchors) == 1
    return anchors[0]


@pytest.fixture()
def circular_fixture(tmp_path: Path) -> Path:
    """Fixture repo containing generated wiki output plus a symlink alias.

    Mirrors the external reference auditor's circular-evidence selftest
    control design: generated output under ``openwiki/`` and a symlink alias
    (``alias.md``) whose lexical path dodges the denied prefix but whose
    resolved path lands inside it.
    """
    fixture = tmp_path / "fixture"
    (fixture / "openwiki").mkdir(parents=True)
    (fixture / "src").mkdir()
    (fixture / "openwiki/generated.md").write_text("Generated wiki output.\n")
    (fixture / "src/real.py").write_text("REAL = 1\n")
    (fixture / "alias.md").symlink_to(Path("openwiki") / "generated.md")
    return fixture


class TestCircularEvidence:
    DENY = ("openwiki",)

    def test_direct_anchor_into_denied_dir(self, circular_fixture: Path) -> None:
        check = check_anchor(
            circular_fixture,
            anchor_for("(src: openwiki/generated.md `Generated wiki output.`)"),
            circular_denylist=self.DENY,
        )
        assert check["status"] == "circular_evidence"

    def test_symlink_alias_into_denied_dir(self, circular_fixture: Path) -> None:
        # The denylist applies AFTER symlink resolution: a lexical path
        # outside the denied prefix that resolves into it is caught.
        check = check_anchor(
            circular_fixture,
            anchor_for("(src: alias.md `Generated wiki output.`)"),
            circular_denylist=self.DENY,
        )
        assert check["status"] == "circular_evidence"

    def test_empty_denylist_preserves_current_behavior(
        self, circular_fixture: Path
    ) -> None:
        for text in (
            "(src: openwiki/generated.md `Generated wiki output.`)",
            "(src: alias.md `Generated wiki output.`)",
        ):
            assert (
                check_anchor(circular_fixture, anchor_for(text))["status"]
                == "resolved"
            )

    def test_denied_source_file_outside_prefix_still_resolves(
        self, circular_fixture: Path
    ) -> None:
        check = check_anchor(
            circular_fixture,
            anchor_for("(src: src/real.py `REAL = 1`)"),
            circular_denylist=self.DENY,
        )
        assert check["status"] == "resolved"

    def test_denylist_is_path_prefix_not_string_prefix(
        self, tmp_path: Path
    ) -> None:
        fixture = tmp_path / "fixture"
        (fixture / "openwiki-notes").mkdir(parents=True)
        (fixture / "openwiki-notes/real.md").write_text("Real source.\n")
        check = check_anchor(
            fixture,
            anchor_for("(src: openwiki-notes/real.md `Real source.`)"),
            circular_denylist=("openwiki",),
        )
        assert check["status"] == "resolved"

    def test_trailing_slash_entry_is_normalized(
        self, circular_fixture: Path
    ) -> None:
        check = check_anchor(
            circular_fixture,
            anchor_for("(src: openwiki/generated.md `Generated wiki output.`)"),
            circular_denylist=("openwiki/",),
        )
        assert check["status"] == "circular_evidence"

    @pytest.mark.parametrize(
        "entry", ["", "   ", "/openwiki", "../openwiki", "openwiki/../src", "."]
    )
    def test_invalid_denylist_entry_fails_closed(
        self, circular_fixture: Path, entry: str
    ) -> None:
        with pytest.raises(CircularDenylistInvalid):
            check_anchor(
                circular_fixture,
                anchor_for("(src: src/real.py `REAL = 1`)"),
                circular_denylist=(entry,),
            )


CIRCULAR_WIKI_PAGE = """---
type: Reference
---

# Circular

Aliased (src: alias.md `Generated wiki output.`).
"""


class TestCircularEvidenceVerdict:
    def test_evaluate_page_plumbs_denylist(self, circular_fixture: Path) -> None:
        case = evaluate_page(
            circular_fixture,
            "circular.md",
            CIRCULAR_WIKI_PAGE.encode(),
            circular_denylist=("openwiki",),
        )
        assert case["passed"] is False
        assert case["checks"]["anchors"][0]["status"] == "circular_evidence"
        assert "anchor[0] alias.md: circular_evidence" in case["failures"]

    def test_evaluate_wiki_flags_circular_and_names_failure(
        self, circular_fixture: Path, tmp_path: Path
    ) -> None:
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "circular.md").write_text(CIRCULAR_WIKI_PAGE)
        permissive = evaluate_wiki(
            wiki, circular_fixture, fixture_sha=FIXTURE_SHA
        )
        assert permissive["passed"] is True
        denied = evaluate_wiki(
            wiki,
            circular_fixture,
            fixture_sha=FIXTURE_SHA,
            circular_denylist=("openwiki",),
        )
        assert denied["passed"] is False
        assert (
            "anchor[0] alias.md: circular_evidence"
            in denied["cases"][0]["failures"]
        )

    def test_explicit_empty_denylist_is_byte_identical(self) -> None:
        # An empty denylist preserves current behavior exactly: same verdict
        # bytes as not passing the parameter at all.
        default = evaluate_wiki(WIKI_GOOD, FIXTURE_REPO, fixture_sha=FIXTURE_SHA)
        explicit = evaluate_wiki(
            WIKI_GOOD, FIXTURE_REPO, fixture_sha=FIXTURE_SHA, circular_denylist=()
        )
        assert canonical_bytes(default) == canonical_bytes(explicit)


class TestAnchorResolution:
    def test_resolved_anchor_carries_target_digest(self) -> None:
        check = check_anchor(
            FIXTURE_REPO, anchor_for("(src: src/demo.py `MAGIC_THRESHOLD = 42`)")
        )
        assert check["status"] == "resolved"
        assert check["target_sha256"].startswith("sha256:")
        assert len(check["target_sha256"]) == len("sha256:") + 64

    def test_traversal_outside_fixture_is_rejected(self) -> None:
        check = check_anchor(FIXTURE_REPO, anchor_for("(src: ../outside.txt `x`)"))
        assert check["status"] == "path_escapes_fixture"

    def test_absolute_path_is_rejected(self) -> None:
        check = check_anchor(FIXTURE_REPO, anchor_for("(src: /etc/passwd `root`)"))
        assert check["status"] == "path_escapes_fixture"

    def test_missing_file_is_its_own_state(self) -> None:
        check = check_anchor(FIXTURE_REPO, anchor_for("(src: src/nope.py `x`)"))
        assert check["status"] == "file_missing"

    def test_fabricated_quote_is_quote_not_found(self) -> None:
        check = check_anchor(
            FIXTURE_REPO, anchor_for("(src: src/demo.py `RETRY_BUDGET = 9`)")
        )
        assert check["status"] == "quote_not_found"

    def test_directory_target_is_not_a_regular_file(self) -> None:
        check = check_anchor(FIXTURE_REPO, anchor_for("(src: src `demo`)"))
        assert check["status"] == "not_a_regular_file"

    def test_symlink_escaping_fixture_is_rejected(self, tmp_path: Path) -> None:
        fixture = tmp_path / "fixture"
        fixture.mkdir()
        (tmp_path / "outside.txt").write_text("secret\n")
        (fixture / "link.txt").symlink_to(tmp_path / "outside.txt")
        check = check_anchor(fixture, anchor_for("(src: link.txt `secret`)"))
        assert check["status"] == "symlink_escapes_fixture"

    def test_quote_outside_line_ref_fails(self) -> None:
        check = check_anchor(
            FIXTURE_REPO, anchor_for("(src: src/demo.py:1 `MAGIC_THRESHOLD = 42`)")
        )
        assert check["status"] == "quote_outside_line_ref"
        good = check_anchor(
            FIXTURE_REPO, anchor_for("(src: src/demo.py:3 `MAGIC_THRESHOLD = 42`)")
        )
        assert good["status"] == "resolved"

    def test_inverted_line_ref_is_invalid(self) -> None:
        check = check_anchor(FIXTURE_REPO, anchor_for("(src: src/demo.py:9-3 `x`)"))
        assert check["status"] == "invalid_line_ref"


class TestVerdict:
    def test_good_wiki_passes_with_scope_fields(self) -> None:
        verdict = evaluate_wiki(WIKI_GOOD, FIXTURE_REPO, fixture_sha=FIXTURE_SHA)
        assert verdict["schema_version"] == "anchor-oracle-verdict@1"
        assert verdict["fixture_sha"] == FIXTURE_SHA
        assert verdict["scope"] == "lexical-only"
        assert verdict["llm_judge_authority"] == "advisory_only"
        assert verdict["passed"] is True
        assert [case["case_id"] for case in verdict["cases"]] == [
            "architecture/overview.md",
            "index.md",
        ]
        for case in verdict["cases"]:
            assert case["passed"] is True
            assert case["failures"] == []
            assert case["evidence_digest"].startswith("sha256:")

    def test_hollow_wiki_fails_with_per_anchor_diagnostics(self) -> None:
        verdict = evaluate_wiki(WIKI_HOLLOW, FIXTURE_REPO, fixture_sha=FIXTURE_SHA)
        assert verdict["passed"] is False
        cases = {case["case_id"]: case for case in verdict["cases"]}
        assert all(not case["passed"] for case in cases.values())

        fabricated = cases["fabricated.md"]
        assert fabricated["checks"]["anchors"][0]["status"] == "quote_not_found"
        assert any(
            "src/demo.py" in failure and "quote_not_found" in failure
            for failure in fabricated["failures"]
        )

        missing = cases["missing.md"]
        statuses = [a["status"] for a in missing["checks"]["anchors"]]
        assert statuses == ["file_missing", "path_escapes_fixture"]
        assert any("anchor[0]" in failure for failure in missing["failures"])

        prose = cases["prose.md"]
        assert prose["checks"]["anchor_total"] == 0
        assert "no_anchors" in prose["failures"]

        badfm = cases["badfm.md"]
        assert badfm["checks"]["frontmatter"]["valid"] is False
        assert "frontmatter:missing_type" in badfm["failures"]
        assert badfm["checks"]["malformed_anchors"]

    def test_good_page_quoting_anchor_grammar_passes(self, tmp_path: Path) -> None:
        # End-to-end guard for the extract_anchors false positive: a page whose
        # single well-formed, resolving anchor quotes text containing "(src:"
        # must pass, not be reported malformed.
        fixture = tmp_path / "fixture"
        fixture.mkdir()
        (fixture / "grammar.md").write_text(
            "Anchors look like (src: path `quote`) tokens.\n"
        )
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "index.md").write_text(
            "---\ntype: Reference\n---\n\n# Grammar\n\n"
            "See (src: grammar.md `look like (src: path`).\n"
        )
        verdict = evaluate_wiki(wiki, fixture, fixture_sha=FIXTURE_SHA)
        assert verdict["cases"][0]["failures"] == []
        assert verdict["passed"] is True

    def test_case_evidence_digest_is_canonical_json_sha(self) -> None:
        verdict = evaluate_wiki(WIKI_GOOD, FIXTURE_REPO, fixture_sha=FIXTURE_SHA)
        case = verdict["cases"][0]
        import hashlib

        record = {
            "case_id": case["case_id"],
            "checks": case["checks"],
            "failures": case["failures"],
            "passed": case["passed"],
        }
        expected = "sha256:" + hashlib.sha256(canonical_bytes(record)).hexdigest()
        assert case["evidence_digest"] == expected

    def test_verdict_is_deterministic(self) -> None:
        first = evaluate_wiki(WIKI_GOOD, FIXTURE_REPO, fixture_sha=FIXTURE_SHA)
        second = evaluate_wiki(WIKI_GOOD, FIXTURE_REPO, fixture_sha=FIXTURE_SHA)
        assert canonical_bytes(first) == canonical_bytes(second)

    def test_fixture_tamper_changes_digest(self, tmp_path: Path) -> None:
        fixture = tmp_path / "fixture_repo"
        shutil.copytree(FIXTURE_REPO, fixture)
        before = evaluate_wiki(WIKI_GOOD, fixture, fixture_sha=FIXTURE_SHA)
        with (fixture / "src/demo.py").open("ab") as handle:
            handle.write(b"# tampered\n")
        after = evaluate_wiki(WIKI_GOOD, fixture, fixture_sha=FIXTURE_SHA)
        assert after["passed"] is True  # quotes still resolve
        digests_before = {c["case_id"]: c["evidence_digest"] for c in before["cases"]}
        digests_after = {c["case_id"]: c["evidence_digest"] for c in after["cases"]}
        assert digests_before["index.md"] != digests_after["index.md"]
        assert (
            digests_before["architecture/overview.md"]
            != digests_after["architecture/overview.md"]
        )

    def test_page_tamper_changes_digest(self, tmp_path: Path) -> None:
        wiki = tmp_path / "wiki"
        shutil.copytree(WIKI_GOOD, wiki)
        before = evaluate_wiki(wiki, FIXTURE_REPO, fixture_sha=FIXTURE_SHA)
        with (wiki / "index.md").open("ab") as handle:
            handle.write(b"\nextra prose.\n")
        after = evaluate_wiki(wiki, FIXTURE_REPO, fixture_sha=FIXTURE_SHA)
        assert (
            before["cases"][1]["evidence_digest"]
            != after["cases"][1]["evidence_digest"]
        )

    def test_empty_wiki_and_missing_wiki_are_distinct(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        with pytest.raises(EmptyWiki):
            evaluate_wiki(empty, FIXTURE_REPO, fixture_sha=FIXTURE_SHA)
        with pytest.raises(WikiMissing):
            evaluate_wiki(tmp_path / "absent", FIXTURE_REPO, fixture_sha=FIXTURE_SHA)
        assert not issubclass(EmptyWiki, WikiMissing)
        assert not issubclass(WikiMissing, EmptyWiki)

    def test_unreadable_page_is_explicit_state(self, tmp_path: Path) -> None:
        # An unreadable input is an environment absence, not a lexical
        # verdict: it must surface as its own fail-closed state instead of
        # an uncaught traceback.
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        page = wiki / "page.md"
        page.write_text("---\ntype: Reference\n---\nbody\n")
        page.chmod(0)
        try:
            with pytest.raises(UnreadableInput):
                evaluate_wiki(wiki, FIXTURE_REPO, fixture_sha="test")
        finally:
            page.chmod(0o644)

    def test_missing_fixture_repo_is_explicit(self, tmp_path: Path) -> None:
        with pytest.raises(FixtureMissing):
            evaluate_wiki(WIKI_GOOD, tmp_path / "absent", fixture_sha=FIXTURE_SHA)


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture()
def git_fixture(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE_REPO, repo)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    git("add", "-A", cwd=repo)
    git(
        "-c",
        "user.email=oracle@test",
        "-c",
        "user.name=oracle",
        "commit",
        "-qm",
        "pin fixture",
        cwd=repo,
    )
    return repo, git("rev-parse", "HEAD", cwd=repo)


class TestFixtureHeadGate:
    def test_matching_head_passes(self, git_fixture: tuple[Path, str]) -> None:
        repo, head = git_fixture
        verify_fixture_head(repo, head)

    def test_mismatched_head_fails_closed(self, git_fixture: tuple[Path, str]) -> None:
        repo, _head = git_fixture
        with pytest.raises(FixtureShaMismatch):
            verify_fixture_head(repo, FIXTURE_SHA)

    def test_non_git_fixture_is_unverifiable(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain"
        plain.mkdir()
        with pytest.raises(FixtureHeadUnverifiable):
            verify_fixture_head(plain, FIXTURE_SHA)

    def test_fixture_inside_enclosing_repo_is_unverifiable(self) -> None:
        # A fixture dir that is not its own repository must not "verify"
        # against the enclosing repository's HEAD: the pin would attest
        # nothing about the fixture's bytes.
        enclosing_head = subprocess.run(
            ["git", "-C", str(PROJECT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        with pytest.raises(FixtureHeadUnverifiable):
            verify_fixture_head(FIXTURE_REPO, enclosing_head)

    def test_dirty_fixture_fails_closed(self, git_fixture: tuple[Path, str]) -> None:
        # Uncommitted tampering must not pass the HEAD gate: HEAD attests
        # committed bytes only.
        repo, head = git_fixture
        (repo / "tampered.txt").write_text("dirty\n")
        with pytest.raises(FixtureDirty):
            verify_fixture_head(repo, head)


class TestTrackedPages:
    """Hand-authored pages living in openwiki/ (an agent-managed lane) must
    keep their anchors resolving: an openwiki rerun may edit page bodies, and
    this gate turns silent anchor drift into a red test."""

    TRACKED = ["openwiki/qualification-pipeline.md"]

    @pytest.mark.parametrize("relpath", TRACKED)
    def test_tracked_page_anchors_resolve(self, relpath: str) -> None:
        from anchor_oracle import evaluate_page

        page = PROJECT / relpath
        case = evaluate_page(PROJECT, page.name, page.read_bytes())
        assert case["passed"] is True, case["failures"]
        anchors = case["checks"]["anchors"]
        assert anchors, "tracked page must carry at least one anchor"
        assert all(a["status"] == "resolved" for a in anchors)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ, PYTHONPATH=str(PROJECT))
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        env=env,
    )


class TestCli:
    def test_good_run_writes_verdict_and_exits_zero(
        self, git_fixture: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, head = git_fixture
        output = tmp_path / "verdict.json"
        result = run_cli(
            "--wiki-dir",
            str(WIKI_GOOD),
            "--fixture-repo",
            str(repo),
            "--fixture-sha",
            head,
            "--output",
            str(output),
        )
        assert result.returncode == 0, result.stderr
        verdict = json.loads(output.read_text())
        assert verdict["schema_version"] == "anchor-oracle-verdict@1"
        assert verdict["fixture_sha"] == head
        assert verdict["scope"] == "lexical-only"
        assert verdict["llm_judge_authority"] == "advisory_only"
        assert verdict["passed"] is True

    def test_hollow_run_exits_two_but_writes_verdict(
        self, git_fixture: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, head = git_fixture
        output = tmp_path / "verdict.json"
        result = run_cli(
            "--wiki-dir",
            str(WIKI_HOLLOW),
            "--fixture-repo",
            str(repo),
            "--fixture-sha",
            head,
            "--output",
            str(output),
        )
        assert result.returncode == 2
        assert json.loads(output.read_text())["passed"] is False

    def test_sha_mismatch_fails_closed_without_output(
        self, git_fixture: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, _head = git_fixture
        output = tmp_path / "verdict.json"
        result = run_cli(
            "--wiki-dir",
            str(WIKI_GOOD),
            "--fixture-repo",
            str(repo),
            "--fixture-sha",
            FIXTURE_SHA,
            "--output",
            str(output),
        )
        assert result.returncode == 3
        assert "mismatch" in result.stderr.lower()
        assert not output.exists()

    def test_non_git_fixture_fails_closed(self, tmp_path: Path) -> None:
        result = run_cli(
            "--wiki-dir",
            str(WIKI_GOOD),
            "--fixture-repo",
            str(FIXTURE_REPO),
            "--fixture-sha",
            FIXTURE_SHA,
            "--output",
            str(tmp_path / "verdict.json"),
        )
        assert result.returncode == 3

    def test_empty_wiki_is_distinct_cli_state(
        self, git_fixture: tuple[Path, str], tmp_path: Path
    ) -> None:
        repo, head = git_fixture
        empty = tmp_path / "empty"
        empty.mkdir()
        result = run_cli(
            "--wiki-dir",
            str(empty),
            "--fixture-repo",
            str(repo),
            "--fixture-sha",
            head,
            "--output",
            str(tmp_path / "verdict.json"),
        )
        assert result.returncode == 3
        assert "empty" in result.stderr.lower()

    def test_missing_arguments_are_usage_errors(self) -> None:
        result = run_cli("--wiki-dir", str(WIKI_GOOD))
        assert result.returncode == 64

    def test_selftest_passes(self) -> None:
        result = run_cli("--selftest")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "hollow" in result.stdout.lower()

    def test_selftest_has_circular_evidence_control(self) -> None:
        result = run_cli("--selftest")
        assert result.returncode == 0, result.stdout + result.stderr
        assert "circular" in result.stdout.lower()


@pytest.fixture()
def circular_git_fixture(tmp_path: Path) -> tuple[Path, str, Path]:
    """Committed fixture repo with generated output + symlink alias, plus a
    wiki whose only anchor cites the generated output through the alias."""
    repo = tmp_path / "repo"
    (repo / "openwiki").mkdir(parents=True)
    (repo / "openwiki/generated.md").write_text("Generated wiki output.\n")
    (repo / "alias.md").symlink_to(Path("openwiki") / "generated.md")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    git("add", "-A", cwd=repo)
    git(
        "-c",
        "user.email=oracle@test",
        "-c",
        "user.name=oracle",
        "commit",
        "-qm",
        "pin circular fixture",
        cwd=repo,
    )
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "circular.md").write_text(CIRCULAR_WIKI_PAGE)
    return repo, git("rev-parse", "HEAD", cwd=repo), wiki


class TestCliCircularDenylist:
    def test_flag_turns_generated_citation_into_failure(
        self, circular_git_fixture: tuple[Path, str, Path], tmp_path: Path
    ) -> None:
        repo, head, wiki = circular_git_fixture
        output = tmp_path / "verdict.json"
        result = run_cli(
            "--wiki-dir",
            str(wiki),
            "--fixture-repo",
            str(repo),
            "--fixture-sha",
            head,
            "--output",
            str(output),
            "--circular-denylist",
            "openwiki",
        )
        assert result.returncode == 2, result.stderr
        verdict = json.loads(output.read_text())
        assert verdict["passed"] is False
        anchor = verdict["cases"][0]["checks"]["anchors"][0]
        assert anchor["status"] == "circular_evidence"

    def test_without_flag_current_behavior_is_preserved(
        self, circular_git_fixture: tuple[Path, str, Path], tmp_path: Path
    ) -> None:
        repo, head, wiki = circular_git_fixture
        output = tmp_path / "verdict.json"
        result = run_cli(
            "--wiki-dir",
            str(wiki),
            "--fixture-repo",
            str(repo),
            "--fixture-sha",
            head,
            "--output",
            str(output),
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(output.read_text())["passed"] is True

    def test_invalid_denylist_entry_is_usage_error(
        self, circular_git_fixture: tuple[Path, str, Path], tmp_path: Path
    ) -> None:
        repo, head, wiki = circular_git_fixture
        output = tmp_path / "verdict.json"
        result = run_cli(
            "--wiki-dir",
            str(wiki),
            "--fixture-repo",
            str(repo),
            "--fixture-sha",
            head,
            "--output",
            str(output),
            "--circular-denylist",
            "../escape",
        )
        assert result.returncode == 64
        assert not output.exists()
