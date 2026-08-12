---
type: Reference
title: Qualification pipeline — structure and dataflow
description: Directory structure and dataflow of the landed slices, with source anchors; contract implementation and physical execution evidence remain separate states.
tags: [architecture, dataflow, qualification]
---

# Qualification pipeline — structure and dataflow

This page tracks what has actually run and where each part lives. Every
factual claim carries a source anchor; this page is itself checkable by the
anchor oracle it documents.

## Directory structure

```
agent-skills-repo/
├── anchor_oracle/                  # deterministic anchoring oracle
├── scripts/
│   ├── anchor_oracle.py            # thin CLI over the oracle
│   ├── check_landing_evidence.py
│   ├── run_sandbox_case.py         # OpenShell contract CLI
│   └── sandbox_case_runner.py      # uploaded deterministic task runner
├── skill_arena/
│   ├── core.py                     # admission, hard gates, receipts
│   ├── skill_assets.py             # artifact digests + corpus guards
│   ├── landing_evidence.py         # completion-evidence validator
│   └── sandbox_executor/           # contract, adapter, signing, publication
├── contracts/
│   └── sandbox-executor.schema.json
├── data/
│   ├── project/landing-evidence.json
│   ├── sandbox_cases/smoke-python.json
│   ├── sandbox_profiles/
│   │   ├── no-network.policy.yaml
│   │   └── openshell-0.0.59-docker.json
│   └── verification_runs/openshell_executor_status.json
├── docs/sandbox-executor.md
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
        │
        ▼
✅ OpenShell executor contract + CLI + signed bundle writer + contract tests
        │
        ▼
⛔ real gateway-backed run ×2 + receipt admission + cleanup/tamper evidence
        │
        ▼
⛔ calibration run → human-frozen budgets
        → qualification run + human admit → first qualification receipt
```

The executor now has a reviewable contract surface, but the physical run is a
separate evidence state. Contract tests do not advance calibration or
qualification by themselves.

## Anchored claims

- The anchoring CLI separates verdict failure from absence states in its exit
  codes (src: scripts/anchor_oracle.py `Exit codes: 0 = verdict passed, 1 = selftest control failure`).
- Machine verdicts never grant the LLM judge authority: gate results pin it
  (src: skill_arena/core.py `"llm_judge_authority": "advisory_only",`).
- The fixture HEAD gate refuses uncommitted tampering as its own state
  (src: anchor_oracle/core.py `class FixtureDirty(AnchorOracleError):`).
- The exportable corpus carries only a pointer to the blind pool
  (src: skills/repo_wiki_verified/corpus.json `"cases_file": "tests/fixtures/blind_seed/blind_cases.json",`).
- A mechanical guard fails closed if blind seed content ever reaches the
  exportable corpus (src: skill_arena/skill_assets.py `def assert_corpus_exportable(`).
- Completion requires a reachable commit plus digested paths and tests
  (src: data/project/landing-evidence.json `"completion_rule":"reachable-commit-plus-digested-paths-and-tests"`).
- The validator derives the path set from the commit rather than trusting prose
  (src: skill_arena/landing_evidence.py `def actual_changed_paths(`).
- The executor package describes itself as fail-closed evidence machinery
  (src: skill_arena/sandbox_executor/__init__.py `"""Fail-closed sandbox execution and signed Arena evidence bundles."""`).
- The CLI declares one pinned OpenShell contract run and signed bundle output
  (src: scripts/run_sandbox_case.py `"""Run one OpenShell 0.0.59 sandbox case and emit a signed evidence bundle."""`).
- Executor failures retain distinct machine exit states rather than collapsing
  into success (src: skill_arena/sandbox_executor/errors.py `class ExitCode(IntEnum):`).
- The committed status explicitly records that physical integration has not run
  (src: data/verification_runs/openshell_executor_status.json `"real_integration": "not_executed",`).
- The same status explicitly forbids qualification use
  (src: data/verification_runs/openshell_executor_status.json `"qualification_eligible": false,`).
- Contract CI names its own non-evidentiary boundary
  (src: .github/workflows/sandbox-executor-contract.yml `Assert contract CI is not physical integration evidence`).
- The contract document requires two fresh physical runs before issue #3 can
  close (src: docs/sandbox-executor.md `two runs must show distinct workspace nonces and no residue before`).
- An unreachable commit can never satisfy a completed item
  (src: skill_arena/landing_evidence.py `completed commit is not reachable from`).

## Tracking contract

- `data/project/landing-evidence.json` is the completion authority. Issue state,
  comments, checkboxes, and future GitHub Project fields are projections.
- **This page** tracks product-side structure and dataflow; update it in the
  same PR as any change to the boundary between contract, executed evidence,
  calibration, and qualification.
- A `completed` item requires a full commit reachable from `main`, the exact
  first-parent changed paths, and digested test evidence; the validator checks
  all three against Git history.
- Issue #3 remains open after the contract lands. It can close only after the
  real OpenShell gateway-backed evidence bundle, second fresh-workspace run,
  receipt admission, tamper rejection, cleanup proof, and private-key absence
  proof are reviewable and recorded by the repository authority.
- Calibration (#5) and qualification (#6) cannot consume executor-backed
  evidence while the committed status remains `real_integration: not_executed`.
