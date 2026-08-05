---
type: Reference
title: Entrypoint Matrix
description: Every hook and workflow with its exact argv, path filter, environment-variable format and side effects — plus the scripts nothing triggers automatically.
tags: [operations, ci, triggers]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [trigger-matrix, ci-coverage, opt-in-variables]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Entrypoint Matrix

What actually runs, when, with which arguments. Read this before assuming a script is covered.

## Local hooks

Both require `git config core.hooksPath .githooks`. They are per-clone and not enforced server-side.

| Hook | Runs | Argv | Side effects |
|---|---|---|---|
| `.githooks/commit-msg` | `scripts/validate_commit_message.py` | `<message-file>` | none |
| `.githooks/pre-push` | `scripts/git_gate.py` | none | none; exits 125 if any gate mutated the tree |

## Workflows

| Workflow | Trigger | Path filter | Commands | Side effects |
|---|---|---|---|---|
| `skill_ci.yml` | pull_request | `skills/**`, `scripts/**` | `python scripts/git_gate.py` | none |
| `wiki_graph_sync.yml` | push, pull_request, workflow_dispatch | `openwiki/**/*.md`, `data/wiki_graph/schema.json`, `scripts/sync_wiki_to_graph.py`, `scripts/check_wiki_graph_sync.py` | `sync_wiki_to_graph.py --wiki-root openwiki --schema … --event-log … --graph-out … --commit-sha "$GITHUB_SHA"`, then `check_wiki_graph_sync.py` | **uploads** `wiki-graph-local-projection` as an artifact; does **not** commit the regenerated files |
| `autoresearch_eval.yml` | pull_request, workflow_dispatch | `skills/autoresearch_composer/**`, `data/autoresearch_golden/**`, `data/autoresearch_traces/**`, `scripts/eval_autoresearch_composer.py`, `scripts/sample_autoresearch_traces.py`, `tests/test_autoresearch_eval_suite.py` | `eval_autoresearch_composer.py --dataset data/autoresearch_golden/pr_golden_set.json`, `sample_autoresearch_traces.py`, `pytest -q -m evals` | none |
| `weekly_audit.yml` | cron `0 3 * * 1`, workflow_dispatch | — | `python scripts/ablation_engine.py` | none |

Note what `weekly_audit.yml` does **not** run: `scripts/real_driver_ablation.py`. The weekly audit is
deterministic and simulated; nothing schedules a real-agent measurement.

## Opt-in variables and their exact formats

This is where two mismatches live. Formats are not interchangeable.

| Variable | Read by | Accepted value | Set by |
|---|---|---|---|
| `ENABLE_GRAPH_DB_WRITE` | `sync_wiki_to_graph.py::write_external_graph_if_enabled()` | the string `"true"` | `vars.ENABLE_GRAPH_DB_WRITE` in `wiki_graph_sync.yml` — **matches** |
| `--write-external-graph` | CLI flag on the same script | present/absent | passed only by the opt-in job |
| `GRAPH_DB_KIND` | same | `generic-http-json` or `neo4j-http`; anything else raises | `vars.GRAPH_DB_KIND`, defaulting to `generic-http-json` |
| `GRAPH_DB_URI` / `GRAPH_DB_USER` / `GRAPH_DB_PASSWORD` | same | required together; a missing one raises naming which | repository secrets |
| `ENABLE_LLM_JUDGE` | `eval_autoresearch_composer.py::cloud_judge()` | the string `"1"` | `autoresearch_eval.yml` passes `vars.ENABLE_CLOUD_EVALS`, i.e. `"true"` — **mismatch, the branch never activates** |
| `OPENAI_API_KEY` | same | required only when `ENABLE_LLM_JUDGE == "1"` | repository secret |
| `GIT_GATE_FIXED_ELAPSED_MS` | `git_gate.py::elapsed_ms()` | integer string; overrides all timings | tests, for reproducible receipts |
| `REPO_EVIDENCE_COLLECTOR_PRODUCTION` | the TypeScript evidence-cost collector | `1` | manual only |

The external graph write needs **both** `--write-external-graph` and `ENABLE_GRAPH_DB_WRITE=true`;
either alone is a no-op. The cloud judge is unreachable from CI even when enabled, and would return a
placeholder `SKIP` if it were reached — see
[Behavioral eval and judge](../validation/behavioral-eval-and-judge.md).

## The 23 gates `git_gate.py` covers

Listed in order on [Defense gate chain](../architecture/defense-gate-chain.md).

## Scripts nothing triggers automatically

These are **not** in `GATES` and not in any workflow. Someone must run them.

| Script | Run it when | Documented on |
|---|---|---|
| `check_plan_package_compat.py` | `PROJECT-SSOT.md` / `plan-package.compat.yaml` / `.plan-package.lock.yaml` changed | [Plan package compatibility](../governance/plan-package-compatibility.md) |
| `test_plan_package_compat.sh` | same | same |
| `check_prompt_trace_assets.py` | `data/prompt_trace/**` changed | [Prompt trace assets](../nonofficial/prompt-trace-assets.md) |
| `sync_wiki_to_graph.py` | a wiki page changed and you want the local projection refreshed | [Wiki graph sync](../nonofficial/wiki-graph-sync-architecture.md) |
| `real_driver_ablation.py` | you are about to claim a real behavioral delta | [Ablation and benchmark](../validation/ablation-and-benchmark.md) |
| `render_lifecycle_openwiki.py --write` | `data/lifecycle/**` changed (the gate runs the compare mode, not the write mode) | [Data authority](../architecture/data-authority.md) |

`check_prompt_trace_assets.py` is covered indirectly by
`tests/test_skill_asset_governance.py::test_static_defense_scripts_pass`, which runs every static gate
independently — so `pytest` catches things a `git push` does not.

## Gates that run argument-less and prove only a selftest

`validate_commit_message.py` and `validate_goal_constraints.py`. See
[Static skill validators](../validation/static-skill-validators.md).

## Opt-in jobs: activation conditions and permissions

Both conditional jobs are gated on a **repository variable**, not a secret, so enabling them is a
visible configuration change:

| Job | Condition | Permissions | Secrets read |
|---|---|---|---|
| `wiki_graph_sync.yml` → `local-first-wiki-graph-sync` | always, on the path filter | `contents: read`, `actions: read` | none |
| `wiki_graph_sync.yml` → `external-graph-write-disabled-by-default` | `${{ vars.ENABLE_GRAPH_DB_WRITE == 'true' }}` | `contents: read` | `GRAPH_DB_URI`, `GRAPH_DB_USER`, `GRAPH_DB_PASSWORD` |
| `autoresearch_eval.yml` → `local-first-eval` | always, on the path filter | default | none |
| `autoresearch_eval.yml` → `cloud-judge-disabled-by-default` | `${{ vars.ENABLE_CLOUD_EVALS == 'true' }}` | default | `OPENAI_API_KEY` |

The external-write job is the only one granted access to graph secrets, and it holds `contents: read`
only — it cannot push the projection back.

## What does NOT trigger CI

Path filters are narrow, and several substantial areas fall outside every one of them. Changing any of
these produces **no** pull-request check:

| Path | Why it matters |
|---|---|
| `.agents/skills/repo-terminal-operator/**` | the entire ~9.7k-line vendored TypeScript operator |
| `.githooks/**` | the hooks themselves; a broken hook is not caught until someone pushes |
| `.github/workflows/**` | a workflow editing itself does not re-trigger on its own path filter |
| `data/wiki_graph/event_log.jsonl`, `data/wiki_graph/sample_graph.json` | the projection artifacts; only `schema.json` is in the filter |
| `data/prompt_trace/**` | `check_prompt_trace_assets.py` is in no filter and no `GATES` list |
| `data/lifecycle/**` | reached only indirectly, when `scripts/**` changes trigger `skill_ci` |
| `data/commit_lineage/**`, `data/verification_runs/**` | lineage ledger and receipts |
| `PROJECT-SSOT.md`, `plan-package.compat.yaml`, `.plan-package.lock.yaml` | the project contract itself |
| `skills/*/status.json` | promotion decisions |
| `tests/**` except `tests/test_autoresearch_eval_suite.py` | the governance suite |

`python3 -m pytest -q` covers most of these locally, which is why the runbook treats it as the
pre-release check rather than an optional extra. See [Usage](../nonofficial/usage.md).

## Coverage summary

| Surface | Covered by |
|---|---|
| every static gate, independently, reporting all failures | `pytest tests/test_skill_asset_governance.py` |
| the 23-gate chain, stopping at the first failure | `git_gate.py`, hook and `skill_ci.yml` |
| golden-dataset and trace assets | `autoresearch_eval.yml`, `pytest -m evals` |
| the wiki→graph projection | `wiki_graph_sync.yml` |
| a simulated weekly delta | `weekly_audit.yml` |
| **a real agent's behavior** | nothing automatic — only a manual `real_driver_ablation.py` run |
