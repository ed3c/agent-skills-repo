#!/usr/bin/env python3
"""Thin CLI for the deterministic anchoring oracle.

Exit codes: 0 = verdict passed, 1 = selftest control failure, 2 = verdict
failed (some case failed a lexical check), 3 = absence / fail-closed state
(missing or empty wiki, missing fixture, unverifiable, dirty or mismatched
fixture HEAD, unreadable input), 64 = usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from anchor_oracle import (
    CircularDenylistInvalid,
    EmptyWiki,
    FixtureDirty,
    FixtureHeadUnverifiable,
    FixtureMissing,
    FixtureShaMismatch,
    UnreadableInput,
    WikiMissing,
    evaluate_wiki,
    verify_fixture_head,
)

EXIT_FAILED = 2
EXIT_ABSENT = 3
EXIT_USAGE = 64

GOOD_PAGE = """---
type: Reference
title: Selftest good page
openwiki_generated: true
---

# Selftest good page

The resolver prefixes case ids
(src: src/demo.py `return "resolved:" + case_id`).
The threshold is pinned (src: src/demo.py:3 `MAGIC_THRESHOLD = 42`).
"""

HOLLOW_FABRICATED = """---
type: Reference
title: Fabricated quote
---

# Fabricated quote

The module defines a retry budget (src: src/demo.py `RETRY_BUDGET = 9`).
The scheduler lives here (src: src/scheduler.py `class Scheduler`).
Secrets live outside (src: ../outside.txt `secret`).
"""

HOLLOW_PROSE = """---
type: Reference
title: Anchorless prose
---

# Anchorless prose

Well-formed prose that cites nothing and therefore proves nothing.
"""

FIXTURE_DEMO = '''"""Selftest fixture module."""

MAGIC_THRESHOLD = 42


def resolve_case(case_id: str) -> str:
    return "resolved:" + case_id
'''

# Circular-evidence control (mirrors the external reference auditor's
# symlink-aliased selftest design): generated wiki output stored inside the
# fixture, cited both directly and through a symlink alias whose lexical
# path dodges the denied prefix.
CIRCULAR_PAGE = """---
type: Reference
title: Circular evidence page
---

# Circular evidence page

Direct citation (src: openwiki/generated.md `Generated wiki output.`).
Aliased citation (src: alias_generated.md `Generated wiki output.`).
"""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def _control(name: str, passed: bool, detail: str = "") -> bool:
    suffix = f" ({detail})" if detail else ""
    print(f"selftest {'PASS' if passed else 'FAIL'}: {name}{suffix}")
    return passed


def run_selftest() -> int:
    """Built-in good/hollow positive controls with zero external inputs."""
    ok = True
    with tempfile.TemporaryDirectory(prefix="anchor-oracle-selftest-") as root:
        base = Path(root)
        fixture = base / "fixture"
        (fixture / "src").mkdir(parents=True)
        (fixture / "src/demo.py").write_text(FIXTURE_DEMO)
        good_wiki = base / "wiki-good"
        good_wiki.mkdir()
        (good_wiki / "index.md").write_text(GOOD_PAGE)
        hollow_wiki = base / "wiki-hollow"
        hollow_wiki.mkdir()
        (hollow_wiki / "fabricated.md").write_text(HOLLOW_FABRICATED)
        (hollow_wiki / "prose.md").write_text(HOLLOW_PROSE)

        good = evaluate_wiki(good_wiki, fixture, fixture_sha="selftest")
        ok &= _control(
            "good wiki passes every lexical check", good["passed"] is True
        )
        ok &= _control(
            "verdict declares lexical-only scope",
            good["scope"] == "lexical-only"
            and good["llm_judge_authority"] == "advisory_only",
        )

        hollow = evaluate_wiki(hollow_wiki, fixture, fixture_sha="selftest")
        cases = {case["case_id"]: case for case in hollow["cases"]}
        statuses = [
            check["status"]
            for check in cases["fabricated.md"]["checks"]["anchors"]
        ]
        ok &= _control("hollow wiki fails", hollow["passed"] is False)
        ok &= _control(
            "hollow failures name anchor and check",
            statuses
            == ["quote_not_found", "file_missing", "path_escapes_fixture"],
            detail=",".join(statuses),
        )
        ok &= _control(
            "anchorless prose is an explicit no_anchors failure",
            "no_anchors" in cases["prose.md"]["failures"],
        )

        (fixture / "openwiki").mkdir()
        (fixture / "openwiki/generated.md").write_text("Generated wiki output.\n")
        (fixture / "alias_generated.md").symlink_to(
            Path("openwiki") / "generated.md"
        )
        circular_wiki = base / "wiki-circular"
        circular_wiki.mkdir()
        (circular_wiki / "circular.md").write_text(CIRCULAR_PAGE)

        permissive = evaluate_wiki(circular_wiki, fixture, fixture_sha="selftest")
        ok &= _control(
            "empty denylist keeps generated-output anchors resolved",
            permissive["passed"] is True,
        )
        denied = evaluate_wiki(
            circular_wiki,
            fixture,
            fixture_sha="selftest",
            circular_denylist=("openwiki",),
        )
        denied_statuses = [
            check["status"]
            for check in denied["cases"][0]["checks"]["anchors"]
        ]
        ok &= _control(
            "denylist flags direct and symlink-aliased circular evidence",
            denied["passed"] is False
            and denied_statuses == ["circular_evidence", "circular_evidence"],
            detail=",".join(denied_statuses),
        )

        digest_before = good["cases"][0]["evidence_digest"]
        with (fixture / "src/demo.py").open("ab") as handle:
            handle.write(b"# tampered\n")
        tampered = evaluate_wiki(good_wiki, fixture, fixture_sha="selftest")
        ok &= _control(
            "fixture byte tamper changes the evidence digest",
            tampered["cases"][0]["evidence_digest"] != digest_before,
        )

        empty = base / "wiki-empty"
        empty.mkdir()
        try:
            evaluate_wiki(empty, fixture, fixture_sha="selftest")
            ok &= _control("empty wiki is an explicit failure state", False)
        except EmptyWiki:
            ok &= _control("empty wiki is an explicit failure state", True)
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    parser = _Parser(description=__doc__)
    parser.add_argument("--wiki-dir")
    parser.add_argument("--fixture-repo")
    parser.add_argument("--fixture-sha")
    parser.add_argument("--output")
    parser.add_argument(
        "--circular-denylist",
        action="append",
        default=[],
        metavar="PREFIX",
        help=(
            "fixture-relative path prefix holding generated wiki output;"
            " anchors resolving into it (after symlink resolution) fail as"
            " circular_evidence. Repeatable. Omitted entirely = check"
            " disabled (no silent default)."
        ),
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)

    if args.selftest:
        return run_selftest()
    required = ("wiki_dir", "fixture_repo", "fixture_sha", "output")
    if any(getattr(args, name) is None for name in required):
        parser.error(
            "--wiki-dir, --fixture-repo, --fixture-sha and --output are"
            " required unless --selftest is given"
        )

    try:
        head = verify_fixture_head(args.fixture_repo, args.fixture_sha)
        verdict = evaluate_wiki(
            args.wiki_dir,
            args.fixture_repo,
            fixture_sha=head,
            circular_denylist=args.circular_denylist,
        )
    except CircularDenylistInvalid as exc:
        parser.error(str(exc))
    except (
        WikiMissing,
        EmptyWiki,
        FixtureMissing,
        FixtureDirty,
        FixtureShaMismatch,
        FixtureHeadUnverifiable,
        UnreadableInput,
    ) as exc:
        print(f"anchor_oracle: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ABSENT

    output = Path(args.output)
    output.write_text(json.dumps(verdict, ensure_ascii=False, indent=2) + "\n")
    written = json.loads(output.read_text())
    if written != verdict:
        print("anchor_oracle: verdict round-trip mismatch", file=sys.stderr)
        return EXIT_ABSENT
    print(
        f"anchor_oracle: {'passed' if verdict['passed'] else 'failed'}"
        f" ({verdict['page_count']} pages) -> {output}"
    )
    return 0 if verdict["passed"] else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
