# anchor_oracle dual-reference comparison (issue #2 acceptance)

Side-by-side run of the new deterministic checker against both references. Verdicts are
listed per fixture and per tool; every divergence is adjudicated individually — nothing
is averaged, and reference verdicts never override the oracle's contract by majority.

- **Ours** — `scripts/anchor_oracle.py` (package `anchor_oracle/core.py`). Exit codes:
  0 passed / 1 selftest-control failure / 2 verdict failed / 3 absence (fail-closed) / 64 usage.
- **Ref A** — internal `kb-ingest/verify-claims.sh` (repo-wiki pre-verifier; dialect:
  kb-ingest front matter `title/repo/commit/page_type/covers/generated_at: null`,
  anchors `(src: path[:line])`, verbatim quotes as blockquotes `> TEXT (src: path:line)`).
  Exit codes: 0 pass / 1 hard fail / 2 FATAL missing input.
- **Ref B** — ed3c auditor `harness/verify.sh` → `src/audit_wiki.ts` @ df2f360 (dialect:
  `(src: path \`verbatim quote\`)`, **no** line refs; aggregate gates anchor_rate ≥85%,
  lexical_validity 100%, entrypoint_coverage ≥30/32). Exit codes: 0 pass / 2 fail /
  3 incomplete / 64 usage.

Provenance of the runs:

- Selftests first, both green: `anchor_oracle.py --selftest` exit 0 (7 controls PASS);
  ed3c `selftest.sh` exit 0 (`PASS(valid/hollow/malformed/symlink/limits/final-retry/claim-preservation/breaker controls)`),
  run from a scratch copy so the read-only reference tree is not mutated.
- Shared fixtures: ed3c wiki arms vs pinned fixture repo @`59a1f214fae1ccd06cc18aa8e923f3263d353c1d`;
  project `tests/fixtures/anchor_oracle/{wiki_good,wiki_hollow}` vs its `fixture_repo`,
  driven through `evaluate_wiki` directly (`fixture_sha` is pass-through there). The runs
  originally noted an "enclosing worktree HEAD" pin for this non-git fixture dir; the
  post-review `verify_fixture_head` now rejects exactly that layout (own-repo + clean-tree
  required), so CLI-path runs use per-run temp git fixtures instead — see the head-gate
  tests. The comparison results themselves are unaffected.
- Raw outputs: scratchpad `work/matrix/` (`ours-*.json`, `kb-*.md`, `ed3c-*.out`).

## Verdict table (fixture × tool)

| Fixture | Ours (exit / verdict) | Ref A kb-ingest (exit / verdict) | Ref B ed3c verify.sh (exit / verdict) |
|---|---|---|---|
| arm-a-baseline | 2 failed — 44/44 pages fail: `no_anchors` ×44, `frontmatter` ×11 | 1 FAIL — H1=82, H2=0, H4=3; **dialect mismatch** (not-comparable in substance) | 2 failed — `anchor_rate 0.0% < 85%` |
| arm-b-retrofit | 2 failed — `frontmatter` ×11, `no_anchors` ×10, **1 hollow anchor** (`nonofficial/production-bottlenecks.md` → `data/commit_lineage/gcr_molecular_commits.json`, quote_not_found); 485 anchors resolved | 1 FAIL — H1=82, **H2=485 (every backtick anchor bounced as "path missing")**, H3 quotes seen=0; not-comparable | 2 failed — `anchor_lexical_validity 99.8%`: **the identical hollow anchor, same page, same reason** |
| arm-b-stripped | 2 failed — `no_anchors` ×44, `frontmatter` ×11 | 1 FAIL — dialect mismatch; not-comparable | 2 failed — `anchor_rate 0.0% < 85%` |
| arm-c-generated | 2 failed — `frontmatter` ×11, `no_anchors` ×22; **590/590 anchors resolved incl. 2 anchors into `openwiki/nonofficial/structured-lifecycle-data.md`** | 1 FAIL — dialect mismatch; not-comparable | 2 failed — `anchor_rate 27.2%`, plus **2 invalid: "circular evidence: openwiki is generated wiki output, not source"** (the same 2 anchors ours resolves) |
| arm-d-gate-driven | 2 failed — `frontmatter` ×10, `no_anchors` ×21; 1053/1053 anchors resolved | 1 FAIL — dialect mismatch; not-comparable | 2 failed — `anchor_rate 59.8% < 85%` (80 unanchored claims, all in `nonofficial/`) |
| wiki_good | **0 passed** — 2 pages, 3/3 anchors resolved (incl. line-ref `src/demo.py:7`) | 1 FAIL — H1=10 (kb keys absent), H2 bounces backtick anchors; not-comparable | 2 failed — vacuous `anchor_rate 0/0→0% < 85%`, vacuous `entrypoint_coverage 0/0→0% < 93.75%`, and `src/demo.py:7` rejected as "file does not exist" (its dialect bans line refs) |
| wiki_hollow | 2 failed — every page fails for its planted reason: `quote_not_found`, `file_missing`, `path_escapes_fixture`, `malformed_anchor`, `no_anchors`, `frontmatter` | 1 FAIL — but **for dialect reasons only** (H1 keys + H2 path-missing); its H3 quote check engaged 0 times, so the fabricated quote itself was never tested | 2 failed — same four planted anchor defects detected with matching reasons (`quote not found in that file`, `file does not exist`, `path escapes target`, `malformed anchor`) |
| *(probe)* missing wiki dir | 3 — explicit `WikiMissing` | 2 — `FATAL: wiki dir not found` (distinct state — OK) | 64 — reported "wiki directory does not exist" via the usage exit code |
| *(probe)* empty wiki dir | 3 — explicit `EmptyWiki` | **0 PASS** — `pages=0 … verdict=PASS` (absence conflated with success) | 2 failed, `complete=true, pages=0` (absence conflated with a genuine "checked and failed") |

Agreement worth stating before the divergences: on the shared backtick-anchor dialect the
two lexical engines (ours and Ref B) agree anchor-for-anchor — the single fabricated
anchor in arm-b-retrofit is flagged by both with the same page/path/reason, and all four
planted defects in wiki_hollow get matching reason codes. Every disagreement below is a
policy/aggregation/scope difference, not a disagreement about whether a quote is in a file.

## Divergences and adjudications

### D1 — wiki_good: ours PASS vs both references FAIL

Ref A fails it on its own front-matter contract (`repo/commit/page_type/covers/generated_at`)
and bounces every backtick anchor at H2 as a missing path; its quote check (H3) never ran
(quotes-seen=0). Ref B fails it on two vacuous thresholds — with 0 C1-shaped claims,
`anchor_rate = 0/0 → 0% < 85%`, and with 0 recognized entrypoints in the 2-file fixture repo,
`entrypoint_coverage = 0/0 → 0% < 93.75%` — plus it rejects the `src/demo.py:7` line-ref
anchor because the ed3c experiment's contract (PROMPT.md) forbids line numbers.

**Adjudication: ours is correct for the arena oracle.** A wiki whose every anchor lexically
resolves, whose front matter validates, and whose pages each carry evidence must pass a
lexical-only oracle. Ref A's verdict is a different pipeline's contract (not-comparable);
Ref B's thresholds are calibrated to one specific experiment repo (hardcoded 30/32
entrypoint baseline) and its 0/0→0%→fail arithmetic makes "nothing measured" fail as if
it were "measured and bad" — fail-closed by accident, wrong verdict and wrong failure
state for a foreign fixture. The line-ref rejection is a deliberate ed3c policy choice,
not an error, but the arena anchor syntax explicitly allows optional line refs, so
accepting `path:line` is correct here.

### D2 — arm-b-retrofit / arm-d-gate-driven: published ed3c baseline PASS vs both live runs FAIL (measured-set divergence)

`arms-baseline.json` records arm-b-retrofit and arm-d-gate-driven as exit 0 — but that
baseline was pinned by `audit_arms.ts`, which invokes the auditor with `--exclude nonofficial`.
The documented `verify.sh` mode measures all pages and fails both arms (reproduced against
both the pinned fixture @59a1f21 **and** ed3c's own repo-snapshot @df2f360, with identical
numbers — so this is measured-set policy, not fixture drift). Reproducing the exclusion
(`audit_wiki.ts … --exclude nonofficial`) reproduces the baseline exactly (31/37 pages, PASS).
The kicker: the one genuinely fabricated anchor in arm-b-retrofit lives in
`nonofficial/production-bottlenecks.md` — inside the excluded set. The published PASS
certified an arm containing a fabricated quote it never measured.

**Adjudication: our full-measured-set behavior is correct for the arena oracle.** An oracle
that silently narrows its measured set can be gamed by moving hollow claims into the
excluded directory. If a caller ever needs an exclusion, it must appear in the verdict
(as ed3c's `measured_set`/`excluded` fields do — good design worth keeping), but the arena
default must be all pages, and our checker caught the real defect precisely because of that.

### D3 — arm-c-generated: 2 anchors into generated wiki output — Ref B flags "circular evidence", ours resolves them

`lifecycle/structured-datasets.md` and `skill-assets/contract.md` both anchor to
`openwiki/nonofficial/structured-lifecycle-data.md` — a generated wiki file inside the
fixture repo. Ref B rejects these ("circular evidence: openwiki is generated wiki output,
not source"; it even carries selftest controls for symlink-aliased variants). Ours resolves
them: the path exists in the pinned fixture and the quote is verbatim, so lexically they
check out. Neither verdict flips on this fixture (both tools fail arm-c anyway), but on
another fixture this could turn a hollow PASS into a certified one.

**Adjudication: Ref B's behavior is the correct one for the arena oracle, and this is a
recorded hardening gap in ours.** A skill author who writes prose into a generated doc and
then anchors claims to that prose has manufactured self-referential evidence; a checker
meant to gate arena verdicts should refuse it. It stays consistent with our lexical-only
scope because the rule is itself lexical (path-prefix denylist of generated-output dirs,
plus symlink-resolution the way Ref B does it). Until implemented, our verdict JSON's
`scope: "lexical-only"` + `llm_judge_authority: "advisory_only"` wording remains literally
true — resolution is not semantic support — but the gap should be closed rather than
lawyered. Follow-up: add a `circular_evidence` anchor status (its own explicit state, not
a re-use of `file_missing`).

### D4 — front-matter semantics: three different contracts

On every arm, ours emits `frontmatter` failures (`missing_opening_delimiter` ×10 —
the `index.md` stubs with no front-matter block — plus one `missing_type`); Ref A demands
its kb-ingest keys and so fails 82 H1 items per arm; Ref B checks no front matter at all.

**Adjudication: ours is correct for the arena oracle.** The oracle's front-matter contract
is OKF v0.1 (reimplemented from `src/okf/frontmatter.ts`), which is the format the arena's
wikis declare. Ref A's keys (`repo:`, `commit:`, `generated_at: null`) are the kb-ingest
pipeline's ingestion contract — right for that pipeline, wrong yardstick here, so its 82
hits per arm are recorded as not-comparable rather than as defects in the arms. Ref B's
silence is simply out of its scope, not a contradiction.

### D5 — Ref A on every backtick-dialect fixture: not-comparable (format mismatch), recorded, not silently skipped

Ref A runs to completion on all 7 fixtures (it is not crashing), but its parser treats
`(src: path \`quote\`)` as a single path token, so **every** such anchor fails H2 as
"anchor path missing in TARGET" (e.g. 485/485 on arm-b-retrofit) and its verbatim-quote
check (H3) engages zero times on all fixtures. Consequence: it returns the same shaped
FAIL for wiki_good and wiki_hollow — it cannot distinguish a fully-anchored wiki from a
fabricated one in this dialect.

**Adjudication: not-comparable: Ref A verifies a different anchor dialect (kb-ingest
blockquote + `path:line`), so its FAIL verdicts on these fixtures carry no signal about
anchor truthfulness and must not be read as adverse findings against the arms or against
ours.** Its per-fixture rows are retained in the table (per issue #2: a divergence row,
never a silent skip). What Ref A does contribute: its H2/H3/H4 decomposition and its
FATAL-on-missing-input behavior, both of which agree with our design.

### D6 — absence semantics: empty wiki PASSes Ref A and "fails" Ref B; only ours reports absence as its own state

Empty wiki dir: Ref A exits 0 with `pages=0 … verdict=PASS` — absence conflated with
success. Ref B exits 2 with `complete=true, pages=0, failures=[anchor_rate 0.0% …]` —
absence conflated with a genuine "checked and failed" (its own `complete=false` machinery
exists but only engages for resource/input limits, not for zero pages). Ours raises
`EmptyWiki`, exit 3. Missing wiki dir: Ref A exits 2 FATAL (correctly distinct), Ref B
reports it but through exit 64 (the usage code), ours exits 3 `WikiMissing`.

**Adjudication: ours is correct, by the oracle's hard design rule** — "every absence is
its own explicit failure state, never conflated with a genuine 'checked and failed'".
Ref A's empty-wiki PASS is the most dangerous behavior in this entire comparison: wired
into a gate, an author could pass by submitting nothing. Ref B at least fails closed but
mislabels the state; a downstream consumer counting "failed" verdicts would book an
absence as a measured failure. This divergence is why exit 3 exists in our contract.

## Bottom line

On the shared dialect the two lexical engines never disagree about a single anchor's
truth. All six divergences are contract-level: measured-set policy (D2), aggregate
thresholds vs per-page checks (D1), circular evidence (D3 — the one place a reference is
right and ours has a gap), front-matter contract (D4), dialect scope (D5), and absence
semantics (D6). One follow-up is recorded: implement circular-evidence rejection
(D3) as an explicit anchor status.
