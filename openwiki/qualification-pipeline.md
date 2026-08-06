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

## Directory structure

```
agent-skills-repo/
├── anchor_oracle/            # deterministic anchoring oracle (slice 1)
├── scripts/
│   ├── anchor_oracle.py      # thin CLI over the oracle
│   ├── check_landing_evidence.py
│   ├── run_sandbox_case.py   # OpenShell host entry
│   └── sandbox_case_runner.py# uploaded, scrubbed in-sandbox runner
├── skill_arena/
│   ├── core.py               # admission, hard gates, receipts
│   ├── skill_assets.py       # artifact digests + corpus guards
│   ├── landing_evidence.py   # completion-evidence validator
│   └── sandbox_executor/     # model, signing, outcomes, OpenShell adapter
├── contracts/
│   ├── sandbox-case-receipt.schema.json
│   ├── sandbox-executor.schema.json
│   └── landing-evidence.schema.json
├── data/
│   ├── project/landing-evidence.json
│   ├── sandbox_cases/smoke-python.json
│   ├── sandbox_profiles/
│   └── verification_runs/openshell_executor_status.json
├── skills/repo_wiki_verified/
│   ├── skills.md
│   ├── manifest.json
│   └── corpus.json
└── tests/
    ├── test_anchor_oracle.py
    ├── test_landing_evidence.py
    ├── test_repo_wiki_verified_corpus.py
    └── test_sandbox_executor.py
```

## Dataflow (executed ✅ / in review 🟡 / pending ⛔)

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
🟡 executor contract + OpenShell 0.0.59 adapter + simulated control-plane tests
        ▼
⛔ real gateway-backed run twice + admitted signed receipt + no-residue evidence
        ▼
⛔ calibration → human-frozen budgets → qualification → human admit
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
- The executor requires a visibly development-scoped key
  (src: skill_arena/sandbox_executor/signing.py `sandbox receipt key id must be visibly development-scoped (dev-*)`).
- A key inside the repository is rejected
  (src: skill_arena/sandbox_executor/signing.py `sandbox receipt private key must live outside the repository`).
- A cleanup failure is a distinct non-signing result
  (src: skill_arena/sandbox_executor/signing.py `sandbox cleanup was not verified`).
- The OpenShell transport profile is pinned
  (src: data/sandbox_profiles/openshell-0.0.59-docker.json `"target_transport_profile": "openshell-cli-create-command@0.0.59",`).
- Contract code is not physical execution evidence; the status remains explicit
  (src: data/verification_runs/openshell_executor_status.json `"real_integration": "not_executed",`).
  It is not qualification eligible (src: data/verification_runs/openshell_executor_status.json `"qualification_eligible": false,`).
- An unreachable commit can never satisfy a completed item
  (src: skill_arena/landing_evidence.py `completed commit is not reachable from`).

## Tracking contract

- `data/project/landing-evidence.json` is the completion authority. Issue state,
  comments, checkboxes, and future GitHub Project fields are projections.
- **This page** tracks product-side structure and dataflow; update it in the
  same PR as any change to the boundary between executed and pending stages.
- A `completed` item requires a full commit reachable from `main`, exact
  changed paths, and digested test evidence; the validator checks all three.
- The executor contract may be reviewed and merged without closing issue #3.
  Issue #3 closes only after real OpenShell runs, receipt admission, tamper
  rejection, external-key custody review, and no-residue evidence are landed.
- Calibration (#5) and qualification (#6) remain blocked until that physical
  integration evidence exists and passes the repository-local authority gate.
