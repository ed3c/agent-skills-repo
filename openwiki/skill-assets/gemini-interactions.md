---
type: Reference
title: gemini_interactions (quarantined)
description: The quarantined Gemini Interactions asset — why a failing real-driver receipt is authoritative over a green 117/117 canary, what the current driver fixed, and what must be re-verified before promotion.
tags: [skill-assets, quarantine, promotion]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [quarantine-decision, real-driver-ablation, canary-limits]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# gemini_interactions — quarantined

> `skills/gemini_interactions/status.json` — `status: quarantined`,
> `production_routable: false`, decided `2026-07-27`.

Read this before routing the asset anywhere. Its static gates are green and its regex canary is
117/117; neither of those is why it is quarantined, and neither overrides the decision.

## The asset

`skills/gemini_interactions/skills.md` routes Gemini Interactions API work: generate or audit code when
the task asks for that API, prefer current Interactions method names, and load
`references/deploy_guide.md` only for deployment detail. It declines Angular components, Vue components,
static data extraction, and unrelated cloud deployment.

## Why it is quarantined

The authority is a **failing receipt**:
`data/verification_runs/gemini_interactions_real_driver_2026-07-27.json`,
`real-driver-ablation-verification-run@0.1.0`, `status: fail`, `decision: A2_FAIL_DO_NOT_PROMOTE`,
model `gpt-5.6-sol`.

| Measurement | Value |
|---|---|
| cases × runs per arm | 10 × 3 → 60 total case runs |
| preregistered threshold | `0.2` |
| raw summary delta | `0.1333` |
| **normalized delta** | **`0.10`** |
| normalized with-skill success rate | `0.5` |
| normalized without-skill success rate | `0.4` |
| agent failures | `4` (exit code 124, timeouts) |
| metadata complete | `false` |
| arm totals | with_skill 15/30 (2 timeouts), without_skill 12/30 (2 timeouts) |

The normalization rule is stated in the receipt: *a case pass requires regex checks to pass and agent
exit_code to equal zero; four timed-out partial outputs are failures.* That is why the normalized delta
(0.10) is lower than the raw one (0.1333) — partial output is not scored as partial credit.

`status.json` records four reasons, and only the first is about the number:

1. normalized delta 0.10 below the preregistered 0.20 threshold;
2. four of sixty case runs timed out, so runtime metadata was incomplete;
3. negative cases forbid their own requested domain words, which confounds correct non-activation with
   refusal to perform the user's task — a **case-corpus defect**, not an asset defect;
4. the completed run shared a repository cwd across samples, so agent-created files were visible to
   later cases and sample independence was contaminated.

Reason 3 is the diagnostic worth carrying forward: the measurement could not distinguish "the skill
correctly did not fire" from "the agent refused the task". Reason 4 says the samples were not
independent, so even the number that was produced is not trustworthy.

`next_action` is explicit: repair the case corpus in a new bounded plan, use the driver's ephemeral
per-invocation cwd with `codex --skip-git-repo-check`, then run a **newly preregistered** ablation —
and *do not rerun this plan a fourth time*.

## Why the green canary does not override it

`scripts/synthetic_case_generator.py` produces 117 fixtures (typescript=59, python=58);
`scripts/interactions_patch_assert_runner.py` reports `total_cases_evaluated=117 passed_cases=117
zero_llm_api_calls=0`; `scripts/local_regex_runner.py` reports `case_count=10 total_trials=50`;
`scripts/ablation_engine.py` on the default gemini cases reports `delta=0.50`.

Every one of those is a **simulation or a regex match**, with zero model calls by construction. The
repository's own compatibility manifest labels the scope honestly:
`p11_current_scope: local-zero-llm-regex-canary` and `synthetic_case_quality_status: insufficient`. See
[Synthetic corpus quality](../validation/synthetic-corpus-quality.md).

One real-agent measurement exists, and it failed.

## What the current driver fixed

`scripts/real_driver_ablation.py` has since been hardened against the exact defects the receipt names.
Each invariant below is proven by a test in `tests/test_real_driver_ablation.py`:

| Invariant | Behavior | Proven by |
|---|---|---|
| the agent command must be templated | `--agent-cmd` without `{task}` exits 2 | `test_agent_command_must_contain_task_placeholder` |
| ephemeral cwd needs the codex flag | a `codex exec` command lacking `--skip-git-repo-check` exits 2 | `test_codex_command_requires_skip_git_repo_check_for_ephemeral_cwd` |
| per-invocation ephemeral cwd | each invocation runs in its own directory, so agent side effects cannot leak between samples | the same test's premise |
| stale resume is rejected | the stored artifact must match `arm`, `case_id`, `run`, the **full expected `argv`**, and `cwd_kind == "ephemeral-temp"`; a mismatch exits 2 naming the offending fields, `cwd_kind` among them | resume-binding test |
| a nonzero agent exit can never pass | exit 3, `telemetry.agent_failures > 0`, `verdict FAIL` | `test_nonzero_agent_exit_can_never_produce_a_passing_ablation` |
| a timeout is a case failure, not a crash | the batch completes and records the failure | `test_agent_timeout_is_recorded_as_failure_instead_of_crashing_batch` |
| partial output is rejected | not scored as partial credit | normalization rule above |
| artifacts cannot escape their root | `safe_artifact_path()` resolves both root and destination and requires `destination.is_relative_to(artifacts_root)`, else `artifact path escapes artifacts root` — the guard on a `case_id` taken from the dataset | path-containment guard |
| the run must prove the skill was loaded | a two-sided nonce probe: the nonce must appear in the with-skill arm **and** be absent from the without-skill arm | `loader_probe.passed` in the report |
| a resumed run cannot be misattributed | `resolved_model` is **re-derived** from session metadata and compared to the stored value, and the receipt path must be a non-symlink file resolving inside `session_root` | receipt-binding check |

The `cwd_kind == "ephemeral-temp"` requirement is the direct structural fix for reason 4 above:
artifacts produced by the old shared-cwd runner are **unresumable by construction**, so a repaired run
cannot silently inherit contaminated samples. Full mechanics on
[Ablation and benchmark](../validation/ablation-and-benchmark.md).

The verdict is five conjuncts —
`loader_probe_passed and metadata_complete and agent_failures == 0 and probe_failures == 0 and delta >= threshold`
— so the 2026-07-27 run would have failed on `metadata_complete` and `agent_failures` even if its delta
had cleared the preregistered bar. The delta is the headline, not the whole gate.

## The quarantine is documented, not enforced

**No gate reads `status.json`.** `scripts/git_gate.py::GATES` does not include a check that binds the
status file to its `verification_receipt`, and no workflow does either. Nothing prevents this asset from
being loaded; the file records a decision a human made.

## Before promotion, all of this must change and be re-verified

1. Repair the negative cases so they no longer forbid their own requested domain words — the corpus
   defect from reason 3. Then `python3 scripts/validator.py`.
2. Preregister a new threshold in a new bounded plan; do not reuse the 2026-07-27 preregistration.
3. Run `scripts/real_driver_ablation.py` with the ephemeral per-invocation cwd and
   `codex --skip-git-repo-check`, with complete runtime metadata and zero timeouts.
4. Write a **new dated** verification run under `data/verification_runs/`; receipts are never edited in
   place.
5. Update `status.json` to reference the new receipt.
6. Optionally, close the enforcement gap by making a gate assert that
   `status.production_routable` is consistent with the referenced receipt's `decision`.

## Validation

```sh
python3 scripts/validator.py
python3 scripts/interactions_patch_assert_runner.py     # canary only — not behavioral evidence
python3 -m pytest -q tests/test_real_driver_ablation.py
```
