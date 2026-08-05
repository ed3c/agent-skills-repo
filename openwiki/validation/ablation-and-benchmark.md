---
type: Reference
title: Ablation and Benchmark
description: The simulated ablation engine, the real-driver ablation that actually calls an agent, and the benchmark matrix — with the boundary between simulated and real evidence stated explicitly.
tags: [ablation, benchmark, evidence]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [ablation-delta, real-driver-evidence, benchmark-matrix]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Ablation and Benchmark

Three measurement tools with very different evidential weight. Confusing them is the main hazard.

## `ablation_engine.py` — simulated, deterministic, in the default chain

Runs the same cases with and without skill context and compares success rates. `TARGET_DELTA = 0.05`;
below that the run exits 2 with `FAIL: ablation delta too small`.

Its "agent" is `simulate_agent()`, a hand-written branch table keyed on `skill_slug` and prompt
substrings. **No model is called.** A new skill slug without a branch there will not be simulated
meaningfully.

`regex_pass()` evaluates `expected_checks` as regexes with one special prefix: a check beginning
`FORBID:` inverts, failing the case when the pattern *is* present.

The delta is dataset-dependent, and quoting it without the dataset is a misreport:

| Dataset | Telemetry |
|---|---|
| `skills/gemini_interactions/cases.json` (default) | `delta=0.50 case_count=10 success_rate=1.0 verdict=PASS` |
| `skills/autoresearch_composer/cases.json` | `delta=1.0 case_count=12 with_skill=1.0 without_skill=0.0`, `positive_case_count=6`, `negative_case_count=6`, `negative_with_skill_success_rate=1.0` |

`scripts/check_autoresearch_lifecycle.py` re-runs the second row and requires all five of those values;
the negative-arm requirement is what stops an always-firing asset from scoring well on positives alone.

Trigger: `.github/workflows/weekly_audit.yml`, Monday 03:00 UTC cron — and it runs **only** this
script, never the real driver.

## `real_driver_ablation.py` — real agent, deliberately outside every automatic trigger

This is the only tool in the repository that produces evidence about an actual agent, and it is in no
`GATES` list and no workflow. It is invoked by hand, spends real agent calls, and its output is what a
promotion decision must cite.

Its hardening is a direct response to a failed run (see
[gemini_interactions](../skill-assets/gemini-interactions.md)), and each invariant has a test in
`tests/test_real_driver_ablation.py`:

- `--agent-cmd` must contain the `{task}` placeholder, else exit 2.
- A `codex exec` command must carry `--skip-git-repo-check`, because each invocation gets an **ephemeral
  per-invocation cwd** — the fix for cross-sample contamination.
- A stale `--resume` is rejected with `cwd_kind` in stderr.
- A nonzero agent exit can never produce a passing ablation: exit 3, `agent_failures > 0`,
  `verdict FAIL`.
- A timeout is recorded as a case failure rather than crashing the batch.
- Partial output is rejected, not given partial credit.
- A loader nonce and a runtime session model receipt bind the run to the model that actually served it.

```sh
python3 scripts/real_driver_ablation.py --agent-cmd 'codex exec --skip-git-repo-check {task}' \
    --runs 3 --threshold 0.2 --artifacts /tmp/ablation --json
```

Preregister the threshold before running. The recorded failure exists because a 0.20 threshold was
preregistered and a 0.10 delta was measured — that only means something because the order was fixed in
advance.

### The loader probe: proving the skill was actually loaded

Before any case runs, the driver invokes the agent **twice** with the same probe task:

> `Loader probe: output EVAL_NONCE exactly if it exists in supplied skill context; otherwise output NO_NONCE.`

once wrapped in the skill context (which carries a freshly generated `nonce`), and once bare. The
verdict is a two-sided condition:

```python
loader_probe_passed = nonce in probe_with["output"] and nonce not in probe_without["output"]
```

Both halves matter. The nonce appearing in the with-skill arm proves the context reached the agent; its
**absence** from the without-skill arm proves the two arms are genuinely different. If the probe fails,
every subsequent delta is measuring nothing, so the run cannot pass regardless of its numbers.

### The verdict is five conjuncts, not one

```python
passed = (loader_probe_passed and metadata_complete
          and agent_failures == 0 and probe_failures == 0
          and delta >= args.threshold)
```

| Conjunct | Meaning |
|---|---|
| `loader_probe_passed` | the skill context demonstrably reached the agent and only the with-skill arm |
| `metadata_complete` | `len(models) == 1` **and** every run resolved to that same model — a run served by a different model invalidates the comparison |
| `agent_failures == 0` | no case exited non-zero; a timeout is exit 124 and counts here |
| `probe_failures == 0` | neither probe invocation itself failed |
| `delta >= threshold` | the preregistered bar |

A green delta with a failed probe, a mixed model set, or a single timeout is still a **fail**. The
report is `real-driver-ablation@0.2.0` and records `inputs` (cases path and SHA-256, skill path and
SHA-256, runs, workers, threshold), the full `loader_probe` block including both probe outputs, and
`runtime` with `resolved_models`, `model_source: runtime-session-metadata` and `metadata_complete`.

### Artifact path containment

Every per-run artifact goes through `safe_artifact_path(root, arm, case_id, f"run-{n}.json")`, which
resolves both the artifacts root and the destination and requires
`destination.is_relative_to(artifacts_root)`, raising
`artifact path escapes artifacts root: <parts>` otherwise. Since `case_id` comes from the dataset, this
is what stops a crafted or careless id containing `..` from writing outside the artifacts tree.

### `--resume` revalidates rather than trusts

`load_resumable_run()` will not reuse a stored artifact until it re-proves every binding.

**Five identity fields must match exactly**, and mismatches are reported by name:

| Field | Expected |
|---|---|
| `arm` | the arm being resumed |
| `case_id` | the case being resumed |
| `run` | the run index |
| `argv` | the **full expected argv** — a changed `--agent-cmd` invalidates the resume |
| `cwd_kind` | the literal `"ephemeral-temp"` — an artifact from the old shared-cwd runner can never be resumed |

That last row is the fix for the contamination that quarantined `gemini_interactions`: artifacts
produced under a shared repository cwd are structurally unresumable, so a repaired run cannot silently
inherit contaminated samples.

**Then the model receipt is re-derived, not read.** The stored `resolved_model_receipt` must be a
string path that is not a symlink, is an existing file, and whose resolved path lies **inside**
`session_root`; `resolved_model` must be a string. The driver then calls
`resolve_model(result["thread_id"], session_root)` afresh and requires both that the re-resolved model
equals the stored `resolved_model` and that the matched receipt path resolves to the same file —
otherwise `existing artifact <path> has invalid model receipt binding`.

So a resumed run cannot be attributed to a model it was not actually served by, and a receipt pointing
outside the session root is rejected rather than followed.

## `benchmark_runner.py` — a fixed matrix, not a measurement

Reports `task_count=100 delta_high_quality=0.15 delta_low_quality=-0.2`. It is a deterministic matrix
describing the expected shape of a quality delta (a good asset helps, a bad asset hurts), not an
observation of this repository's assets. Useful as a regression tripwire on the scoring code; useless as
evidence about a skill.

## `github_skill_harvester.py`

Reports `skill_count` and `suspected_ai` from local files with no network access. Inventory, not
evaluation.

## Which one to cite

| Claim you want to make | Tool that can support it |
|---|---|
| "the case corpus still behaves as specified" | `ablation_engine.py` |
| "the scoring code did not regress" | `benchmark_runner.py` |
| "this skill improves a real agent" | **only** `real_driver_ablation.py`, with a preregistered threshold |

## Validation

```sh
python3 scripts/ablation_engine.py --cases skills/autoresearch_composer/cases.json
python3 scripts/benchmark_runner.py
python3 -m pytest -q tests/test_real_driver_ablation.py
```
