---
type: Reference
title: Qualification pipeline — structure and dataflow
description: Directory structure and dataflow of the landed slices, with source anchors; the executed/pending boundary is tracked here.
tags: [architecture, dataflow, qualification]
---

# Qualification pipeline — structure and dataflow

This page tracks what has actually run and where each part lives. Every
factual claim carries a source anchor; this page is itself checkable by the
anchor oracle it documents.

## Directory structure (landed by slices 1+2)

```
agent-skills-repo/
├── anchor_oracle/            # deterministic anchoring oracle (slice 1)
├── scripts/
│   ├── anchor_oracle.py      # thin CLI over the oracle
│   └── check_landing_evidence.py
├── skill_arena/
│   ├── core.py               # admission, hard gates, receipts (dependency)
│   ├── skill_assets.py       # artifact digests + corpus guards (slice 2)
│   └── landing_evidence.py   # completion-evidence validator
├── contracts/                # receipt and governance JSON schemas
├── data/project/
│   └── landing-evidence.json # repository-local completion authority
├── skills/repo_wiki_verified/
│   ├── skills.md             # the discipline (WHY/HOW/WHEN/WHEN NOT)
│   ├── manifest.json         # recomputable digests, pending-qualification
│   └── corpus.json           # exportable PUBLIC cases only
└── tests/
    ├── test_anchor_oracle.py
    ├── test_landing_evidence.py
    ├── test_repo_wiki_verified_corpus.py
    └── fixtures/             # public fixtures; blind pool is NOT published
```

## Dataflow (executed ✅ / pending ⛔)

```
published QA bank ──seeds──▶ corpus.json (public cases)
pinned fixture repo ──vendored subset──▶ tests/fixtures/repo_wiki_verified/
        │
        ▼
✅ reference wiki pages ──▶ anchor_oracle CLI ──▶ verdict JSON
        │                     (lexical-only; judge advisory-only)
        ▼ real evidence digests
✅ skill_arena admission + hard-gate dry run (DRAFT ppm threshold)
        ▼
⛔ sandbox executor → calibration run → human-frozen budgets
        → qualification run + human admit → first qualification receipt
```

## Anchored claims

- The CLI separates verdict failure from absence states in its exit codes
  (src: scripts/anchor_oracle.py `Exit codes: 0 = verdict passed, 1 = selftest control failure`).
- Machine verdicts never grant the LLM judge authority: gate results pin it
  (src: skill_arena/core.py `"llm_judge_authority": "advisory_only",`).
- The fixture HEAD gate refuses uncommitted tampering as its own state
  (src: anchor_oracle/core.py `class FixtureDirty(AnchorOracleError):`).
- The exportable corpus carries only a pointer to the blind pool
  (src: skills/repo_wiki_verified/corpus.json `"cases_file": "tests/fixtures/blind_seed/blind_cases.json",`).
- A mechanical guard fails closed if blind seed content ever reaches the
  exportable corpus (src: skill_arena/skill_assets.py `def assert_corpus_exportable(`).
- Completion requires a reachable commit plus digested paths and tests
  (src: data/project/landing-evidence.json `"completion_rule": "reachable-commit-plus-digested-paths-and-tests"`).
- The sandbox executor remains pending in the repository authority
  (src: data/project/landing-evidence.json `"title": "Sandbox executor walking skeleton",`).
  Its evidence level is missing (src: data/project/landing-evidence.json `"evidence_level": "missing",`).
- An unreachable commit can never satisfy a completed item
  (src: skill_arena/landing_evidence.py `completed commit is not reachable from`).

## Tracking contract

- `data/project/landing-evidence.json` is the completion authority. Issue state,
  comments, checkboxes, and future GitHub Project fields are projections.
- **This page** tracks product-side structure and dataflow; update it in the
  same PR as any change to the boundary between executed and pending stages.
- A `completed` item requires a full commit reachable from `main`, exact
  changed paths, and digested test evidence; the validator checks all three.
- The sandbox executor remains tracked by open issue #3. Calibration (#5) and
  qualification (#6) cannot claim executor-backed evidence until that landing
  exists and passes the repository-local authority gate.
