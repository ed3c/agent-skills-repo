---
type: Reference
title: autoresearch_composer
description: The state-graph asset — the five routes it selects between, how domain-term recovery works, and where its evidence lives.
tags: [skill-assets, routing, state-graph]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [route-selection, native-yield, domain-term-recovery]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# autoresearch_composer

The repository's most developed asset, registered as `production-seed-candidate`. Its executed evidence
is on [Autoresearch-composer lifecycle](../nonofficial/autoresearch-composer-lifecycle.md); this page covers what
the asset itself does.

## What it prevents

From `skills/autoresearch_composer/skills.md`: plan bounded, metric-driven autoresearch loops *"without
collapsing match, generate, and validate into one prompt. The asset prevents compressed context from
becoming an ambiguous route decision."*

The failure mode is specific. Given a thin prompt, a single-shot planner produces a confident route
anyway. Splitting the work into graph nodes makes the thinness visible as a missing input rather than
absorbing it into a guess.

## Route selection

`scripts/eval_autoresearch_composer.py::simulate_autoresearch_plan()` is the executable model of the
routing rule. It inspects the lowercased prompt and returns one of five routes with the state path it
implies:

| Trigger in the prompt | Route | States |
|---|---|---|
| `debug`, `reproduction` | `native-yield:diagnose` | S0 → S1 → S2 → S4 |
| `security`, `owasp` | `native-yield:security-review` | S0 → S1 → S2 → S4 |
| `unit test`, `failing`, `bug fix` | `native-yield:tdd` | S0 → S1 → S2 → S4 |
| `golden dataset`, `llm-as-a-judge`, `trace`, `evals`, `eval suite` | `/autoresearch:evals` | S0 → S1 → S2 → S3 → S4 |
| anything else | `/autoresearch:plan` | S0 → S1 → S2 → S3 → S4 |

The three `native-yield:*` routes are the *decline* answers — "an existing native skill owns this, do
not compose an autoresearch loop". They skip S3 (generate) entirely, which is the structural expression
of yielding. The two slash routes generate an Iteration-Loop Contract.

Route equality is checked by `deterministic_guardrails()` before any judge runs, so a wrong route is a
hard failure that no score can rescue.

## Domain-term recovery

When the prompt lacks the domain vocabulary the plan needs, the asset recovers it from
`skills/autoresearch_composer/references/state_graph.md` rather than inventing it. The reference is
required by `scripts/check_autoresearch_lifecycle.py` to contain `S1 match`, `S3 generate`,
`S4 validate`, `conditional_edge.S4.ablation_not_positive`, `domain_terms`, and the actor-routing
vocabulary. The corresponding edge in the authoring graph is
`conditional_edge.S3.missing_domain_term`; see [Stateful workflow](../nonofficial/stateful-workflow.md).

## Actor routing

`skills.md` states it directly: *"Actor routing follows judge-loop-chooser: Opus/Codex/agy are assigned
one evidence role each, never left as an unresolved choice."* The same five actors are required in the
prompt-trace record — `codex`, `agy`, `external-verify`, `judge-loop-chooser`, `openwiki` — see
[Prompt trace assets](../nonofficial/prompt-trace-assets.md). Leaving an actor unassigned is the ambiguity the asset
exists to remove; leaving one *unrun* is surfaced as a gate, see
[Semantic arbitration](../validation/semantic-arbitration.md).

## Boundaries

`WHEN`: a task asks for Goal / Scope / Metric / Direction / Verify / Guard / Iterations and keep-discard
optimization.

`WHEN NOT`: general SDLC planning, TDD fixes, security reviews, bug diagnosis, design grilling, family
eval-case authoring. The first four are exactly what the `native-yield:*` routes hand back.

## Cross-repo identity

`scripts/check_autoresearch_lifecycle.py` compares this asset against an upstream source skill two
directories above the repository (`.claude/skills/autoresearch-composer/`) and fails if the two
`cases.json` files differ. The production copy cannot drift from the authored one — but the check
silently reduces in scope when that upstream tree is absent, which is a
[root-local-runtime](../nonofficial/production-bottlenecks.md) dependency.

## Validation

```sh
python3 scripts/check_autoresearch_lifecycle.py
python3 scripts/eval_autoresearch_composer.py --dataset data/autoresearch_golden/pr_golden_set.json
python3 scripts/ablation_engine.py --cases skills/autoresearch_composer/cases.json
```
