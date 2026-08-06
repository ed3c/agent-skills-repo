# Historical calibration provenance

This repository recovered the provenance of a two-stage calibration that was
reported as landed under short SHA `4b2de85`. The full source identity is:

```text
ed3c/skill-bettor@4b2de858097cba50f8320c4fb7570136bb9715a3
```

It is not an `agent-skills-repo` commit and is not delivery authority.

## What is preserved

`data/calibration/historical/skill-bettor-4b2de858.json` records:

- the full source repository and commit;
- every calibration-specific source-tree blob that could be re-read from the
  source commit;
- every calibration path claimed by the commit diff but not readable from the
  source tree through the contents API, without inventing a blob identity;
- the historical stage-2 report digest and non-evidentiary boundary;
- the ten case cost values, case/pass counts, and maximum observed agent wall
  time needed to recompute the reported budgets;
- the host fallback, model-class drift, legacy executor API, and missing-tree
  deviations;
- the conditions required before a new calibration can become qualification
  preregistration.

## Independently recomputed values

The gate uses decimal arithmetic and integer formulas:

```text
target_success_ppm = 9 * 1,000,000 // 10 = 900,000
latency_budget_ms  = 41,898 * 3 // 2    = 62,847
cost_total_usd     = decimal sum of 10 costs = 1.615403
```

These values reproduce the historical frozen artifact. Reproduction does not
make them current authority.

## Quarantine contract

The JSON Schema and Python validator require all of the following to remain
false:

```text
delivery_authority
qualification_eligible
preregistration_eligible
runtime_import_allowed
```

The manifest status is fixed to `historical-unlanded`. A change that flips any
eligibility flag, changes a source blob, changes a cost, changes a formula, or
changes a deviation without recomputing the manifest digest fails CI.

No qualification or calibration runtime should import files from
`data/calibration/historical/`. This directory is evidence about prior work, not
an execution configuration.

## Why some paths are marked unrecoverable

The source commit diff metadata names additional freeze code, reports, schemas,
and tests. Re-reading those paths from the source commit tree returned `Not
Found`, while five calibration-specific blobs remained readable. Diff metadata
is not substituted for tree evidence: unavailable paths are listed with a
reason and no asserted hash.

A future workspace recovery may replace an `unrecoverable_tree_paths` row with a
`recovered_files` row only after the exact bytes and Git blob identity become
readable and the manifest digest is updated through review.

## Fresh calibration path

Issue #31 owns the new authoritative work:

1. land the single sandbox executor contract;
2. admit two physical OpenShell evidence bundles;
3. port calibration to `skill_arena.sandbox_executor`;
4. pin the exact agent path and model intended for qualification;
5. run each public case exactly once and preserve failures;
6. retain raw non-secret attempt evidence;
7. produce a new report and a separate human freeze decision;
8. record the final landing in `data/project/landing-evidence.json`.

Only that fresh landing may unblock qualification issue #6.
