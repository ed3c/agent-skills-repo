---
type: Reference
title: Behavioral Eval and Judge
description: The golden-dataset eval harness, its deterministic guardrails, the local heuristic judge, the double-lock verdict parser, and the fact that the cloud judge makes no API call.
tags: [evals, judge]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [golden-dataset-eval, deterministic-guardrails, judge-double-lock]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Behavioral Eval and Judge

## The harness

`scripts/eval_autoresearch_composer.py` accepts a dataset as a JSON list, a JSON object with a `cases`
key, or JSONL — `--dataset` selects the file, `--mode local|cloud` the judge.

Every case must carry all of `REQUIRED_CASE_FIELDS`: `id`, `prompt`, `expected_route`, `must_include`,
`must_not_include`, `judge_rubric`, `trace_tags`, `risk_level`. `must_include`, `must_not_include` and
`trace_tags` must be `list[str]`. A malformed case is an error, not a skipped case.

## Guardrails run before any judge

`deterministic_guardrails()` returns a failure list built from three checks:

1. the produced route equals `expected_route`;
2. every `must_include` literal appears in the produced text;
3. no `must_not_include` literal appears.

Only then does a judge see the result. This ordering is the design: a judge cannot rescue a wrong route
or a forbidden literal. The judge's job is to grade what survived, not to arbitrate correctness.

## The local judge is a heuristic

`local_llm_as_judge()` assigns `0.96` when the guardrail list is empty, and
`max(0.0, 0.82 - 0.05 × failures)` otherwise; the verdict is `PASS` when the score is at least `0.85`
**and** there were zero guardrail failures. In other words it is a deterministic restatement of the
guardrail result with a number attached — it adds no independent signal. Treat its score as a label,
not as evidence.

## The cloud judge makes no API call

`cloud_judge()` is the `--mode cloud` branch:

- If `ENABLE_LLM_JUDGE != "1"`, it returns `judge_mode: cloud-disabled`, verdict `SKIP`.
- If it is `"1"` but `OPENAI_API_KEY` is unset, it raises — the key is required *only* on that path.
- If both are set, it returns `judge_mode: cloud-placeholder-disabled-in-seed`, verdict `SKIP`,
  reasoning *"cloud/API call path is intentionally not activated in the local-first seed"*.

There is no HTTP request anywhere in that function. The cloud judge is a wired-but-unimplemented seam.

### What a cloud `SKIP` does to the exit code

This is the trap. `main()` appends to `failures` only when
`case_failures or judge["verdict"] == "FAIL"`. A `SKIP` is neither, so:

- the run **exits 0**;
- `passed_cases` is computed as `len(cases) - len(failures)`, so every skipped case counts as
  **passed**;
- `cloud_judge_enabled` is `True` only when `ENABLE_LLM_JUDGE == "1"` *and* `--mode cloud`.

A cloud-mode run in which no case was actually judged therefore reports a full pass. The only signal
that nothing happened is `judge_mode: cloud-disabled` or `cloud-placeholder-disabled-in-seed` inside
`results[]`, which the summary line does not surface. Read `--json` output, not the summary, before
believing a cloud-mode result.

A second, separate fact: `.github/workflows/autoresearch_eval.yml` sets
`ENABLE_LLM_JUDGE: ${{ vars.ENABLE_CLOUD_EVALS }}`, i.e. the string `true`, while the code compares
against `"1"`. Even if a maintainer enabled the repository variable, the branch would take the
`cloud-disabled` path. Both facts matter: the seam is unimplemented **and** the workflow would not reach
it.

## The double-lock verdict parser

`scripts/llm_judge.py` is the component that would accept an *external* judge's answer, and it is
deliberately hostile to malformed input:

1. the response is wrapped in `<judge_output>…</judge_output>` before parsing — an XML shield, so a
   bare payload cannot smuggle structure;
2. the first `{…}` block is extracted and parsed with `parse_constant` raising, so `NaN`, `Infinity`
   and `-Infinity` are rejected rather than silently accepted;
3. the score must be finite;
4. the verdict is accepted only when `verdict == PASS` **and** `score >= 0.85` **and** the reasoning
   contains no `breach detected`.

Point 4 is the double lock: a high score cannot override a stated breach, and a clean reasoning string
cannot override a low score. Anything else exits 2 with `FAIL: double-lock judge rejection`.

## Trace sampling

`scripts/sample_autoresearch_traces.py` validates the local-first trace samples and reports
`sample_count=3 observed_state_count=5 cloud_judge_enabled=false`. Traces stay on the local filesystem;
their privacy classification is on [Prompt trace assets](../nonofficial/prompt-trace-assets.md).

## Validation

```sh
python3 scripts/eval_autoresearch_composer.py --dataset data/autoresearch_golden/pr_golden_set.json
python3 scripts/eval_autoresearch_composer.py --dataset data/autoresearch_golden/nightly_golden_set.jsonl
python3 scripts/llm_judge.py --response '{"verdict":"PASS","score":0.91,"reasoning":"clean"}'
python3 -m pytest -q -m evals
```
