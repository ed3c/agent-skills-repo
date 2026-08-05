---
type: Reference
title: Structured Lifecycle Datasets
description: The data/lifecycle SSOT behind the generated display page, and every cross-consistency rule check_lifecycle_datasets.py enforces across eval summary, privacy, drift, promotion records and the human-admit binding.
tags: [lifecycle, datasets, promotion]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [lifecycle-ssot, cross-consistency-gate, human-admit-binding]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Structured Lifecycle Datasets

`data/lifecycle/` is the machine-readable SSOT for skill promotion state.
[Structured Lifecycle Data](../nonofficial/structured-lifecycle-data.md) is its **generated** display layer — that
page is produced by `scripts/render_lifecycle_openwiki.py` and must stay byte-equal to
`--stdout`, so it cannot carry commentary. This page holds the commentary.

## The seven artifacts

| Path | Holds |
|---|---|
| `skill_optimization_registry.json` | the managed skills and their `current_status` |
| `golden_dataset_versions.json` | dataset versions and case counts |
| `eval_runs/autoresearch_composer_2026-07-23.json` | the recorded run's `result_summary` |
| `promotion_records.json` | promotion decisions and their human-admit binding |
| `dataset_drift_history.jsonl` | per-version drift rows |
| `trace_privacy_classification.json` | per-dataset cloud policy |
| `../autoresearch_traces/failure_trace_samples.jsonl` | the failure traces the drift rows count |

## What `check_lifecycle_datasets.py` enforces

This gate is unusual: it does not merely validate each file, it requires the files to **agree with each
other and with a re-run of the renderer**. Every rule below is a separate failure message.

### Generation

The display page must equal `render_lifecycle_openwiki.py --stdout` exactly, else
`structured lifecycle openwiki must equal renderer output`. Hand-editing it is therefore a gate failure,
not a style issue.

### Registry

Exactly one managed skill, with `skill_id == "autoresearch_composer"` and
`current_status == "production-seed-candidate"`. A second skill or a promoted status fails.

### Dataset versions

| Version | Required case count |
|---|---|
| `autoresearch-pr-golden@2026-07-23` | 4 |
| `autoresearch-nightly-golden@2026-07-23` | 3 |

### Eval-run summary

Seven fields, each pinned:

```text
pr_cases 4 · pr_passed 4 · nightly_cases 3 · nightly_passed 3
trace_samples 3 · failure_trace_samples 2 · ablation_delta 1.0 · status PASS
```

These are the same numbers the live commands report — see
[Autoresearch-composer lifecycle](../nonofficial/autoresearch-composer-lifecycle.md). The gate is what stops the
recorded run and the reproducible run from drifting apart.

### Trace privacy

Root `cloud_upload_allowed` must be `false`, **and** every entry in `datasets[]` must carry
`cloud_allowed: false`. A single dataset opting in fails the gate by name. This is a hard local-only
stance, not a default.

### Drift history

Exactly **2** seed rows. Each row must have `pass_rate == 1.0` and `failure_trace_sample_count == 2`,
must contain a non-empty `route_distribution` mapping, and — the interesting one —
**`sum(route_distribution.values())` must equal that row's `case_count`**. A drift row cannot claim a
route breakdown that fails to account for every case.

### Failure traces

Exactly **2** rows, each with `verdict == "FAIL"` and `cloud_judge_enabled` explicitly `false`. The
failure corpus is local-only by construction.

### Promotion record and the human-admit binding

Exactly one record, with `promotion_status == "candidate_until_human_admit"` and
`human_admit == "required_before_promotion"`.

Its `human_admit_binding` must be a structured object with `binding_status == "prepared_unsigned"` —
prepared, and explicitly not signed. Six keys must be present, and four of them are cross-checked:

| Key | Rule |
|---|---|
| `dataset_versions` | must **equal the promotion record's own** `dataset_versions` |
| `molecular_commit` | must **equal the promotion record's own** `molecular_commit` |
| `plan_package_input_ids` | must be exactly `gcr-047d548-conversation`, `real-synthetic-case-generation-research`, `gcr-37731ad-eval-guardrails-lifecycle`, `gcr-874b5c-event-sourcing-graphrag` |
| `route` | must be `ROUTES.md#plan-package-materialization` |
| `routes_edge_id` | must be `plan-package-materialization` |
| `lineage_edge_id` | must be `EDGE-066` |

The design intent is worth stating plainly: a human admit is not a boolean somebody flips. Before the
signature can even be offered, the record must already bind *which* dataset versions, *which* molecular
commit, *which* four plan-package inputs, *which* route, and *which* lineage edge the promotion would
cover — and the binding's copies must match the record's own. `prepared_unsigned` is the state where
everything is pinned and only the human decision is missing.

### Display literals

Sixteen literals must appear on the generated page, including `4/4`, `3/3`, `delta=1.0`,
`candidate_until_human_admit`, `plan-package-materialization`, `EDGE-066`,
`failure_trace_sample_count` and `route_distribution`. Because that page is generated, these are
effectively assertions about the **renderer**: if `render_lifecycle_openwiki.py` stops emitting one of
them, the gate fails even though the data is intact.

## Changing any of this

```sh
# edit the JSON under data/lifecycle/, then:
python3 scripts/render_lifecycle_openwiki.py --write
python3 scripts/check_lifecycle_datasets.py
```

Editing the display page directly is never the fix. If a number needs to change, change the dataset and
regenerate; if the page's shape needs to change, change the renderer.

Ownership rules for every artifact here: [Data authority](../architecture/data-authority.md).
