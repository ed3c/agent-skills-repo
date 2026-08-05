---
type: Playbook
title: Quickstart
description: Agent entry point for agent-skills-repo — a map of the five planes, a change-area routing table from intent to owning files, tests and minimal validation, and the tracked backlog.
tags: [quickstart, navigation, routing]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [task-routing, repo-map, backlog]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Quickstart

`agent-skills-repo` is a **skill-asset governance seed**: it holds skill assets, the deterministic
scripts that defend them, the structured evidence those scripts read, and a local-first projection of
this wiki into a retrieval graph. Python ≥ 3.11, no third-party runtime dependency.

Two entry points: this page routes an agent to the work;
[nonofficial/README.md](nonofficial/README.md) is the human navigation index required by
`scripts/check_openwiki.py`. This page and the section directories beside it are OpenWiki's own output
shape; everything the repository's gates pin at a fixed path lives under
[nonofficial/](nonofficial/), explained in the [provenance map](nonofficial/provenance.md).

## The one thing to internalise

**Documentation here is load-bearing.** Four gates read pages under `openwiki/`,
`scripts/check_openwiki.py` is one of the 23 gates `git push` runs, and one page must be byte-equal to a
generator's stdout. Editing a page is a code change. Correspondingly, a green gate proves less than it
looks — read [Production bottlenecks](nonofficial/production-bottlenecks.md) before quoting any number here as a
capability claim.

## Map

| Plane | Lives in | Page |
|---|---|---|
| skill assets | `skills/<slug>/` | [Skill asset contract](skill-assets/contract.md) |
| defense gates | `scripts/`, `.githooks/`, `.github/workflows/` | [Defense gate chain](architecture/defense-gate-chain.md) |
| structured evidence | `data/` | [Data authority](architecture/data-authority.md) |
| wiki → graph | `openwiki/` → `data/wiki_graph/` | [Wiki graph sync](nonofficial/wiki-graph-sync-architecture.md) |
| vendored operator | `.agents/skills/repo-terminal-operator/` | [Terminal operator](terminal-operator/overview.md) |

Composition and ownership boundary: [Architecture overview](architecture/overview.md).
Where each artifact in this wiki comes from — OpenWiki's design, a gate that pins it, or the port —
is recorded in the [non-official provenance map](nonofficial/provenance.md).

## Task routing

| Change area / intent | Page | Source entrypoints | Important symbols | Focused tests | Minimal validation |
|---|---|---|---|---|---|
| add or edit a skill's behavior cases | [Skill asset contract](skill-assets/contract.md) | `skills/<slug>/cases.json`, `scripts/validator.py` | `validate_cases()`, `WEAK_PATTERNS` | `test_static_defense_scripts_pass` | `python3 scripts/validator.py` |
| edit a skill's router text | [Skill asset contract](skill-assets/contract.md) | `skills/<slug>/skills.md` | — | `test_static_defense_scripts_pass` | `python3 scripts/skill_description_linter.py` |
| decide whether a skill may be routed | [gemini_interactions](skill-assets/gemini-interactions.md) | `skills/<slug>/status.json`, `data/verification_runs/` | — | none — **no gate reads status.json** | `python3 scripts/real_driver_ablation.py …` |
| add or change a defense gate | [Defense gate chain](architecture/defense-gate-chain.md) | `scripts/git_gate.py` | `GATES`, `input_state_sha256()`, `write_receipt()` | `test_static_defense_scripts_pass` | `python3 scripts/git_gate.py` |
| change the golden dataset or eval logic | [Behavioral eval and judge](validation/behavioral-eval-and-judge.md) | `scripts/eval_autoresearch_composer.py`, `data/autoresearch_golden/` | `deterministic_guardrails()`, `local_llm_as_judge()`, `cloud_judge()` | `tests/test_autoresearch_eval_suite.py` | `python3 -m pytest -q -m evals` |
| change judge acceptance | [Behavioral eval and judge](validation/behavioral-eval-and-judge.md) | `scripts/llm_judge.py` | `extract_json()`, `parse_score()` | `test_static_defense_scripts_pass` | `python3 scripts/llm_judge.py` |
| measure a real behavioral delta | [Ablation and benchmark](validation/ablation-and-benchmark.md) | `scripts/real_driver_ablation.py` | — | `tests/test_real_driver_ablation.py` | `python3 -m pytest -q tests/test_real_driver_ablation.py` |
| edit a wiki page | [Code call lifecycle](nonofficial/code-call-lifecycle.md) | `openwiki/**`, `scripts/check_openwiki.py` | `REQUIRED_FILES`, `REQUIRED_LITERALS` | `test_skill_asset_structure` | `python3 scripts/check_openwiki.py` |
| change the graph projection | [Wiki graph sync](nonofficial/wiki-graph-sync-architecture.md) | `scripts/sync_wiki_to_graph.py`, `data/wiki_graph/schema.json` | `event_for_file()`, `project_graph()` | `test_wiki_graph_sync_gate_passes` | `python3 scripts/sync_wiki_to_graph.py && python3 scripts/check_wiki_graph_sync.py` |
| change lifecycle data | [Data authority](architecture/data-authority.md) | `data/lifecycle/**`, `scripts/render_lifecycle_openwiki.py` | `render()` | `test_autoresearch_eval_and_trace_gates_pass` | `python3 scripts/render_lifecycle_openwiki.py --write && python3 scripts/check_lifecycle_datasets.py` |
| change prompt-trace assets | [Prompt trace assets](nonofficial/prompt-trace-assets.md) | `data/prompt_trace/**` | `REQUIRED_SLOTS`, `REQUIRED_ACTORS` | `test_static_defense_scripts_pass` | `python3 scripts/check_prompt_trace_assets.py` |
| write a commit message | [Molecular commit lineage](governance/molecular-commit-lineage.md) | `.githooks/commit-msg`, `scripts/validate_commit_message.py` | `REQUIRED_FIELDS`, `workspace_root()` | — | `python3 scripts/validate_commit_message.py <file>` |
| change the project contract | [Plan package compatibility](governance/plan-package-compatibility.md) | `PROJECT-SSOT.md`, `plan-package.compat.yaml` | `final_repo_forbidden_paths` | — | `python3 scripts/check_plan_package_compat.py` |
| touch the vendored operator | [Terminal operator](terminal-operator/overview.md) | `.agents/skills/repo-terminal-operator/` | `publishWriterArtifact()`, `executeAsyncWorker()` | **none here** | not verifiable in this checkout |

Everything at once, and what a push runs: `python3 scripts/git_gate.py`.
Full trigger table including the six scripts nothing triggers:
[Entrypoint matrix](operations/entrypoint-matrix.md).

## Conditional and expensive checks

Not part of the default chain. Each has a source-backed condition.

| Command | Condition |
|---|---|
| `python3 -m pytest -q` | before release, or after broad `scripts/` changes — it runs every static gate independently and reports all failures |
| `python3 scripts/real_driver_ablation.py --agent-cmd '… {task}'` | you are about to claim a real behavioral delta; spends real agent calls, needs a preregistered threshold |
| `python3 scripts/validate_molecular_commit_lineage.py --require-current-history` | auditing history; **needs a Git root this directory does not have** |
| `python3 scripts/check_prompt_trace_assets.py --workspace-root … --commit-repo …` | re-verifying frozen inputs against an external workspace |

## Backlog

Tracked gaps, each with its anchor and reason.

1. **`scripts/check_openwiki.py:73` carries a stale expected value.** It requires
   `openwiki/code-call-lifecycle.md` to contain `protected_history=157 compensated=157`, while
   `data/verification_runs/gcr_three_surface_commit_traceability_2026-07-27.json` reports
   `commit_count=235 / compensated_commit_count=235 / failed_commit_count=0`. The page quotes the stale
   expectation explicitly and states the true counts, so the gate passes without asserting a false
   number. Reason not fixed: correcting the gate is a source change, outside a documentation run.
2. **`skills/*/status.json` is not enforced.** No gate binds a quarantine decision to its
   `verification_receipt`. Anchor: [gemini_interactions](skill-assets/gemini-interactions.md).
3. **`.github/workflows/autoresearch_eval.yml` passes an unusable value.** It sets `ENABLE_LLM_JUDGE`
   to the string `true`; `scripts/eval_autoresearch_composer.py::cloud_judge()` accepts only `"1"`.
   Even when reached the branch makes no API call. Anchor:
   [Entrypoint matrix](operations/entrypoint-matrix.md).
4. **Two gates prove only a selftest under `git_gate.py`** — `validate_commit_message.py` and
   `validate_goal_constraints.py` run argument-less. Anchor:
   [Static skill validators](validation/static-skill-validators.md).
5. **Verification wave: closed, 9/9 PASS.** The official `wiki_question_finder` /
   `wiki_answer_verifier` loop was run to convergence. The finder produced 9 source-derived questions
   from repository source alone; the verifier judged them from this wiki alone. Trajectory:

   | Wave | PASS | Still PARTIAL |
   |---|---|---|
   | initial | 0 | Q-01 … Q-09 |
   | retry 1 | Q-05 Q-06 Q-07 Q-09 | Q-01 Q-02 Q-03 Q-04 Q-08 |
   | retry 2 | + Q-03 Q-04 Q-08 | Q-01 Q-02 |
   | retry 3 | + Q-01 | Q-02 |
   | retry 4 | + Q-02 | — |

   Each round narrowed the gap rather than restating it, and the last one was a single missing fact
   (how `leaseExpiresAt` is actually derived at claim time). Transcripts are in `.openwiki-review/`.
   **Residual depth limit, unchanged:** the pages above state contracts, invariants and exact rejection
   rules read from source; they are not a line-by-line reading of every module. `async-worker-carrier.ts`
   (1219 lines) and `validate_molecular_commit_lineage.py` (585 lines) in particular were read at the
   functions the questions reached, not end to end.
6. **The vendored operator cannot be verified here.** No `package.json`, no `tsconfig.json`, no Bun
   toolchain, and its required upstream `runtime/` and `skills/repo-neural-perception/` trees are
   absent. Anchor: [Terminal operator overview](terminal-operator/overview.md).
7. **Evidence-cost v1/v2 handoff is unclosed.** The cache accepts observation `@v1`; the collector emits
   `@v2`. Anchor: [Evidence cost](terminal-operator/evidence-cost.md).
