# repo_wiki_verified — full procedure

The discipline: generate, anchor, verify mechanically, repair, repeat until
pass. A lexical pass is never semantic truth; the oracle's verdict always
carries `scope: "lexical-only"` and `llm_judge_authority: "advisory_only"`.

## 1. Pin the fixture

The fixture repository is the only ground the wiki may claim against.

- The fixture directory must be its OWN git repository (`git rev-parse
  --show-toplevel` equals the fixture path). A fixture nested inside an
  enclosing repository is unverifiable: the enclosing HEAD attests nothing
  about the fixture's bytes.
- The working tree must be clean. HEAD attests committed bytes only, so a
  dirty tree voids the pin (`FixtureDirty`).
- `HEAD` must equal the pinned sha exactly (`FixtureShaMismatch` otherwise).

## 2. Generate into OKF v0.1

Generate pages with the pinned openwiki build. Every page opens with OKF
front matter:

```
---
type: Reference
title: <page title>
description: <one line>
---
```

Front-matter rules (reimplemented from openwiki `src/okf/frontmatter.ts`):
the file begins with a `---` line and closes the block with an exact `---`
line; the block parses as a YAML 1.2 core-schema mapping with unique keys and
alias expansion capped at 100 nodes; `type` is required; every present OKF
string field (`type`, `title`, `description`, `resource`, `timestamp`) is a
non-empty string; `tags`, when present, is a list of non-empty strings.
Unknown producer extension fields are tolerated.

## 3. Anchor every factual claim

Grammar: `(src: relative/path `verbatim quote`)`, with an optional `:N` or
`:N-M` line-ref suffix on the path. The quote must appear byte-for-byte in
the target file (and inside the referenced line range when a line ref is
given). A claim without an anchor is an unverified claim; a page without a
single anchor fails (`no_anchors`).

## 4. Run the mechanical oracle

```
PYTHONPATH=<project> python3 scripts/anchor_oracle.py \
  --wiki-dir <wiki> --fixture-repo <fixture> --fixture-sha <sha> \
  --output verdict.json
```

Exit codes: `0` every page passed; `2` a page failed (verdict still
written); `3` absence — the oracle could not judge at all (missing wiki,
empty wiki, missing/dirty/mismatched fixture, unreadable input) and no
verdict file is written; `64` usage error. Absence is never a verdict.

## 5. Repair table

| Failure | Repair |
| --- | --- |
| `frontmatter:*` | Fix the OKF block per section 2. |
| `malformed_anchor` | Rewrite to the exact grammar; a bare `src:` without `(` is reported, never dropped. |
| `no_anchors` | Anchor at least one claim per page. |
| `path_escapes_fixture` / `symlink_escapes_fixture` | Anchor only paths inside the fixture. |
| `file_missing` / `not_a_regular_file` | Point at a real regular file at the pinned sha. |
| `quote_not_found` | Re-copy the quote byte-for-byte from the pinned file. |
| `invalid_line_ref` / `quote_outside_line_ref` | Correct or drop the `:N-M` suffix. |
| `line_ref_on_undecodable_file` | Drop the line ref; binary targets take whole-file quotes only. |
| `page_not_utf8` | Re-encode the page as UTF-8. |

Repeat run-and-repair until exit 0. Only then hand the wiki to any human or
downstream gate — and hand it as "lexically anchored at <sha>", never as
"verified true".

## 6. Corpus and pool discipline

- Benchmark cases derived from a published QA bank are pool `public`,
  always: both banks of the anchoring experiment are published.
- The blind pool only accepts seeds with no published provenance
  (`admit_candidate` rejects a public seed digest requesting `blind`).
- Hard-gate groups `critical`, `anchor`, `target` all stay non-empty;
  mutation labels come only from `{boundary, semantic_noise,
  constraint_conflict, chain_escalation}`.
- No negative case may forbid vocabulary its own prompt requires; the
  mechanical guard is `skill_arena.skill_assets.
  negative_case_vocabulary_conflicts`.
