---
type: Playbook
title: Autoresearch-Composer Lifecycle
description: What the autoresearch_composer asset optimizes, the executed golden-dataset and ablation evidence behind its production gate, and the hardening it still lacks.
tags: [autoresearch, evals, promotion]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [golden-dataset-eval, ablation-gate, promotion-readiness]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Autoresearch-Composer Lifecycle

`skills/autoresearch_composer/` is the asset this repository has taken furthest through the lifecycle.
Its registry status is `production-seed-candidate` — a candidate, not an admitted asset.

## Concrete Optimization

The asset's stated purpose is to stop a compressed context from silently becoming a route decision. It
forces a plan into a state graph — match, then route, then generate, then validate — and each of those
is a separate node with its own failure edge, rather than one fused prompt that returns one confident
answer. Recovery for a thin prompt is explicit: **missing Domain terms** are recovered through
`skills/autoresearch_composer/references/state_graph.md` rather than guessed, which is why
`conditional_edge.S3.missing_domain_term` exists as a real edge instead of a retry.

The measured consequence is unusually clean. With the asset in context, all 12 behavior cases pass;
without it, none do:

| Arm | Success rate |
|---|---|
| with skill | `1.0` |
| without skill | `0.0` |

## Golden Standard Example

A **Golden Dataset cases** file pins expected behavior per case rather than scoring free text.
`data/autoresearch_golden/pr_golden_set.json` is the pull-request set; each case declares
`expected_route`, `must_include`, `must_not_include`, `judge_rubric`, `trace_tags` and `risk_level`,
and `scripts/eval_autoresearch_composer.py::validate_case_schema()` rejects a case missing any of them.

The evaluation order matters: `deterministic_guardrails()` runs first and checks route equality plus
the literal include/exclude lists; only then does a judge see the result. A route mismatch is a hard
failure that no judge score can rescue.

## Production Gate

Promotion is gated on evidence, not on the asset reading well.

**A/B ablation is a hard gate.** `scripts/check_autoresearch_lifecycle.py` re-runs
`scripts/ablation_engine.py --cases skills/autoresearch_composer/cases.json --json` and fails unless the
telemetry satisfies all of: `case_count == 12`, `delta > 0.05`, `verdict == PASS`,
`negative_case_count == 6`, and `negative_with_skill_success_rate == 1.0`. The negative half is not
decoration — an asset that fires on everything would score well on positives alone, so the gate
requires that the six negative cases are correctly *not* triggered.

The same gate cross-checks the repo asset against its upstream source skill and requires the two
`cases.json` files to be identical, so the production copy cannot drift from the authored one.

## Executed Autoresearch-Composer Data

Reproduced by running each command in the repository root:

| Command | Reported |
|---|---|
| `python3 scripts/eval_autoresearch_composer.py --dataset data/autoresearch_golden/pr_golden_set.json` | `cases=4` `passed=4` `mode=local` `cloud_judge_enabled=false` |
| `python3 scripts/eval_autoresearch_composer.py --dataset data/autoresearch_golden/nightly_golden_set.jsonl` | `cases=3 passed=3 mode=local cloud_judge_enabled=false` |
| `python3 scripts/sample_autoresearch_traces.py` | `sample_count=3` `observed_state_count=5` |
| `python3 scripts/ablation_engine.py --cases skills/autoresearch_composer/cases.json` | `delta=1.0` `case_count=12` `success_rate=1.0` `verdict=PASS` |

The ablation figure is dataset-specific. The same script on its default
`skills/gemini_interactions/cases.json` reports `delta=0.50 case_count=10`. A delta quoted without its
dataset is not a fact about this asset.

## Lifecycle Data Artifacts

The machine-readable side lives under `data/lifecycle/` and is the SSOT; the Markdown view of it is
generated, never hand-edited — see [Structured Lifecycle Data](structured-lifecycle-data.md), and the
cross-consistency rules behind it in
[Structured lifecycle datasets](../lifecycle/structured-datasets.md).

| Artifact | Holds |
|---|---|
| `data/lifecycle/skill_optimization_registry.json` | the managed skill and its `current_status` |
| `data/lifecycle/golden_dataset_versions.json` | `autoresearch-pr-golden@2026-07-23` (4 cases), `autoresearch-nightly-golden@2026-07-23` (3 cases) |
| `data/lifecycle/eval_runs/autoresearch_composer_2026-07-23.json` | the recorded run summary |
| `data/lifecycle/promotion_records.json` | promotion decisions |
| `data/lifecycle/dataset_drift_history.jsonl` | dataset drift over time |
| `data/lifecycle/trace_privacy_classification.json` | per-dataset privacy classification |
| `data/autoresearch_traces/{local_trace_samples,failure_trace_samples}.jsonl` | local-first trace samples |

`scripts/check_lifecycle_datasets.py` enforces that the display page is byte-equal to
`scripts/render_lifecycle_openwiki.py --stdout`, and cross-checks the dataset versions and case counts
above against the registry.

## Missing Future Hardening

Stated plainly, because a candidate that reads as finished is the failure mode this repository is built
against:

- **No real-driver evidence for this asset.** Every number above comes from a deterministic simulation
  (`simulate_autoresearch_plan()`, `simulate_agent()`). `scripts/real_driver_ablation.py` — the runner
  that invokes an actual agent — has never been recorded against `autoresearch_composer`. The one asset
  that was measured that way failed and is quarantined; see
  [gemini_interactions](../skill-assets/gemini-interactions.md).
- **The judge is a heuristic.** `local_llm_as_judge()` assigns `0.96` when no guardrail failed and a
  degrading score otherwise. It adds no independent signal beyond the deterministic guardrails.
- **cloud/API judge execution is physically wired but disabled unless explicitly enabled** — read that
  precisely. The branch exists, `--mode cloud` selects it, and `ENABLE_LLM_JUDGE=1` is the switch. When
  enabled, `cloud_judge()` returns `judge_mode: cloud-placeholder-disabled-in-seed` with verdict `SKIP`
  and makes **no HTTP request**. The wiring is the code path and the environment contract, not a
  provider call. Additionally `.github/workflows/autoresearch_eval.yml` passes the string `true` while
  the code requires `"1"`, so the workflow's opt-in job would not activate even that placeholder.
- **A 12-case corpus is small** for a claim of `delta=1.0`, and the without-skill arm scoring exactly
  `0.0` reflects a simulator built to contrast, not a measurement of a real agent's baseline.

## Validation

```sh
python3 scripts/check_autoresearch_lifecycle.py
python3 scripts/eval_autoresearch_composer.py --dataset data/autoresearch_golden/pr_golden_set.json
python3 -m pytest -q -m evals
```
