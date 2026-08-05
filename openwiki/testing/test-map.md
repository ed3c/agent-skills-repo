---
type: Reference
title: Test Map
description: What each pytest module proves, described by behavior and invariant so the right suite can be retrieved without reading it from the top.
tags: [testing]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [test-coverage-map, eval-markers]
libraries: [pytest]
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Test Map

`pyproject.toml` sets `testpaths = ["tests"]` and registers three markers: `evals` (local-first golden
dataset and deterministic judge), `llm_judge` (cloud path, disabled by default), `trace` (local trace
sampling and guardrails).

```sh
python3 -m pytest -q            # everything
python3 -m pytest -q -m evals   # golden-dataset and judge only
```

## `tests/test_skill_asset_governance.py`

The structural and gate-level suite. Five tests, each proving a different class of invariant:

| Test | Invariant |
|---|---|
| `test_skill_asset_structure` | ~57 required paths exist — the hooks, all four workflows, the twelve `openwiki/` contract paths, the skill assets, the named scripts, and every `data/` artifact. This is the test that turns a deleted file into a red suite. |
| `test_static_defense_scripts_pass` | each of 25 static gates exits 0 **independently**. Unlike `git_gate.py`, it does not stop at the first failure, so it reports every broken gate in one run. It also covers `check_prompt_trace_assets.py`, which `git_gate.py` does not. |
| `test_p11_synthetic_and_zero_llm_telemetry` | the canary telemetry is exact, not merely green — `synthetic_cases=117`, `typescript=59`, `python=58`, `total_cases_evaluated=117`, `passed_cases=117`, `zero_llm_api_calls=0`, `case_count=10`, `total_trials=50`, `task_count=100`, `delta_high_quality=0.15`, `delta_low_quality=-0.2`. A drifting number fails the suite. |
| `test_autoresearch_eval_and_trace_gates_pass` | the PR golden eval passes with `cloud_judge_enabled=false`, the trace sampler passes, and the lifecycle dataset display matches its generator |
| `test_wiki_graph_sync_gate_passes` | a projection built into a **temporary directory** reproduces cleanly, then the gate passes — proving reproducibility without mutating the checked-in artifacts |
| `test_molecular_commit_lineage_gate_passes` | the lineage validator's default structural mode passes |

Note what the last one does not do: it runs the default mode, so it proves ledger structure, not Git
history. See [Molecular commit lineage](../governance/molecular-commit-lineage.md).

## `tests/test_autoresearch_eval_suite.py`

Marked `evals`. Exercises the golden-dataset harness — schema validation, deterministic guardrails, and
the local heuristic judge. This is the suite `.github/workflows/autoresearch_eval.yml` runs with
`-m evals`. See [Behavioral eval and judge](../validation/behavioral-eval-and-judge.md).

## `tests/test_real_driver_ablation.py`

The largest and most behavioral suite (282 lines), covering the driver that actually calls an agent.
Retrieve it by the failure it proves rather than by symbol:

| Behavior proven | Test |
|---|---|
| an agent command without `{task}` is rejected | `test_agent_command_must_contain_task_placeholder` |
| a `codex exec` command without `--skip-git-repo-check` is rejected, because each invocation gets an ephemeral cwd | `test_codex_command_requires_skip_git_repo_check_for_ephemeral_cwd` |
| a stale `--resume` is rejected with `cwd_kind` in stderr | resume-binding test |
| a nonzero agent exit can never produce a passing ablation (exit 3, `agent_failures > 0`, `verdict FAIL`) | `test_nonzero_agent_exit_can_never_produce_a_passing_ablation` |
| a timeout is recorded as a case failure instead of crashing the batch | `test_agent_timeout_is_recorded_as_failure_instead_of_crashing_batch` |

`tests/fixtures/real_driver_mock_agent.py` is the stand-in agent, so these run without spending real
agent calls. See [gemini_interactions](../skill-assets/gemini-interactions.md) for why each invariant
exists.

## No TypeScript tests

There is no test for `.agents/skills/repo-terminal-operator/` in this repository — no `package.json`, no
Bun toolchain, and the suites its contract references live in an absent upstream tree. See
[Terminal operator overview](../terminal-operator/overview.md).
