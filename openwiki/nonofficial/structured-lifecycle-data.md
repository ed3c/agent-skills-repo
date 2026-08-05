# Structured Lifecycle Data

This page is the Markdown display layer for machine-readable lifecycle data.
The structured SSOT lives under `data/lifecycle/`; this page must not invent
numbers that are absent from those files.

## Dataflow

```text
data/lifecycle/skill_optimization_registry.json
  -> data/lifecycle/golden_dataset_versions.json
  -> data/lifecycle/eval_runs/autoresearch_composer_2026-07-23.json
  -> data/lifecycle/promotion_records.json
  -> data/lifecycle/dataset_drift_history.jsonl
  -> data/lifecycle/trace_privacy_classification.json
  -> scripts/render_lifecycle_openwiki.py
  -> openwiki/nonofficial/structured-lifecycle-data.md
  -> scripts/check_lifecycle_datasets.py
```

## Multi-Skill Registry

| skill_id | status | openwiki | eval run | cloud policy |
|---|---|---|---|---|
| `autoresearch_composer` | `production-seed-candidate` | `openwiki/autoresearch-composer-lifecycle.md` | `autoresearch-composer-2026-07-23-local` | `implemented-disabled-by-default` |

## Golden Dataset Versions

| dataset_version | path | cases | use |
|---|---|---:|---|
| `autoresearch-pr-golden@2026-07-23` | `data/autoresearch_golden/pr_golden_set.json` | 4 | pull-request gate |
| `autoresearch-nightly-golden@2026-07-23` | `data/autoresearch_golden/nightly_golden_set.jsonl` | 3 | nightly local holdout |

## Dated Eval Run

| run_id | PR | nightly | trace | failure trace | ablation |
|---|---:|---:|---:|---:|---|
| `autoresearch-composer-2026-07-23-local` | `4/4` | `3/3` | 3 | 2 | `delta=1.0`, `verdict=PASS` |

## Trace Privacy

| dataset | classification | cloud allowed |
|---|---|---|
| `data/autoresearch_traces/local_trace_samples.jsonl` | `synthetic-local` | false |
| `data/autoresearch_traces/failure_trace_samples.jsonl` | `synthetic-local-failure` | false |

## Promotion Record

| record_id | molecular commit | route edge | lineage edge | status | human gate |
|---|---|---|---|---|---|
| `promotion-autoresearch-composer-2026-07-23` | `0006-regression-ci-seed.md` | `plan-package-materialization` | `EDGE-066` | `candidate_until_human_admit` | `required_before_promotion` |

## Drift Metrics

| dataset_version | case_count | pass_rate | judge_score_avg | trace_sample_count | failure_trace_sample_count | route_distribution |
|---|---:|---:|---:|---:|---:|---|
| `autoresearch-pr-golden@2026-07-23` | 4 | 1.0 | 0.96 | 3 | 2 | `autoresearch_evals=1, autoresearch_plan=2, native_yield=1` |
| `autoresearch-nightly-golden@2026-07-23` | 3 | 1.0 | 0.96 | 3 | 2 | `autoresearch_evals=1, native_yield=2` |

## Extension Rule For More Skills

For every new optimized skill, add exactly one registry row, at least one Golden
Dataset version, one dated eval run, one promotion record, trace privacy entries,
and one drift-history row. Then expose the same IDs in openwiki and make
`scripts/render_lifecycle_openwiki.py --write` and
`scripts/check_lifecycle_datasets.py` pass.
