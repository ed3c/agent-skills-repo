---
type: Workflow
title: Stateful Workflow
description: The S1–S7 state graph for authoring and admitting a skill asset, its conditional edges, and why matching, generation, and validation must stay separate nodes.
tags: [workflow, state-graph]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [authoring-state-graph, conditional-edges, separation-of-concerns]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Stateful Workflow

Authoring a skill asset in this repository is a state machine, not a single prompt.
Matching, generation, and validation are separate nodes on purpose: collapsing them lets a compressed
context turn into an unexamined route decision, which is exactly the failure the
`autoresearch_composer` asset exists to prevent (`skills/autoresearch_composer/skills.md`).

```mermaid
stateDiagram-v2
    [*] --> S1
    S1: S1 Match Repo Shape
    S2: S2 Choose Route
    S3: S3 Generate Skill Asset
    S4: S4 Generate Behavior Cases
    S5: S5 Validate Static Defenses
    S6: S6 Evaluate Behavior
    S7: S7 Publish Wiki And Graph
    S1 --> S2
    S2 --> S3
    S3 --> S4
    S4 --> S5
    S5 --> S6
    S6 --> S7
    S7 --> [*]
    S5 --> S3: conditional_edge.S5.baseline_fail
    S6 --> S4: conditional_edge.S6.delta_below_target
    S7 --> S3: conditional_edge.S7.openwiki_fail
```

## S1 Match Repo Shape

Decide whether the task matches this repository's archetype at all. `PROJECT-SSOT.md` fixes it:
`project_archetype: skill-asset-governance-repo`, and explicitly *"not a LangGraph application seed"*.
`README.md` states the ownership boundary — this repo owns skill assets, defense scripts, hooks,
provenance lock and compatibility guards, and does **not** own plan packets, small-loop routes, or
template drafts. `scripts/check_plan_package_compat.py` enforces the negative half through
`final_repo_forbidden_paths`.

## S2 Choose Route

Pick the routing decision before generating anything. For the `autoresearch_composer` asset this is
modelled explicitly by `simulate_autoresearch_plan()` in `scripts/eval_autoresearch_composer.py`, which
emits one of five routes — `native-yield:diagnose`, `native-yield:security-review`,
`native-yield:tdd`, `/autoresearch:evals`, or `/autoresearch:plan`. The native-yield routes are the
"this belongs to an existing native skill" answer; a route mismatch is a hard guardrail failure, not a
judge opinion.

## S3 Generate Skill Asset

Write `skills/<slug>/skills.md` with the four sections (WHY, HOW, WHEN, WHEN NOT) and move deployment
or reference detail into `references/` so the router stays dense. `scripts/skill_description_linter.py`
and `scripts/validate_progressive_disclosure.py` police those two properties;
`scripts/validate_goal_constraints.py` requires goal/constraint phrasing rather than brittle step lists.

## S4 Generate Behavior Cases

Write `skills/<slug>/cases.json` to the baseline in `scripts/validator.py`: 10–20 cases, each with
`id` / `prompt` / `should_trigger` / `expected_checks`, no weak check patterns, at least five positive
and five negative cases, and no two prompts above a 0.85 `SequenceMatcher` similarity ratio. See
[Skill asset contract](../skill-assets/contract.md).

## S5 Validate Static Defenses

Run the deterministic validators. In practice this is `python3 scripts/git_gate.py`, which sequences 23
gates and stops at the first failure. A failure here routes back to S3 or S4
(`conditional_edge.S5.baseline_fail`) rather than forward; the gate is not advisory.

Two of those gates run argument-less under git_gate and therefore prove only their own selftest path —
see [Entrypoint matrix](../operations/entrypoint-matrix.md).

## S6 Evaluate Behavior

Deterministic guardrails plus a judge. `scripts/eval_autoresearch_composer.py` checks route equality
and `must_include` / `must_not_include` literals before any judging, then applies the local heuristic
judge. `scripts/ablation_engine.py` measures the with-skill/without-skill delta against
`TARGET_DELTA = 0.05`. A delta below target routes back to S4
(`conditional_edge.S6.delta_below_target`) because the usual cause is a weak case corpus, not a weak
asset — the concrete instance of that diagnosis is recorded in
[gemini_interactions](../skill-assets/gemini-interactions.md).

## S7 Publish Wiki And Graph

Update the wiki, then project it. `scripts/check_openwiki.py` validates that the required pages exist
and carry their required literals; `scripts/sync_wiki_to_graph.py` projects the Markdown into an event
log and graph, and `scripts/check_wiki_graph_sync.py` validates the result.
`conditional_edge.S7.openwiki_fail` routes a documentation failure back to S3, because in this
repository a missing or stale page is treated as an asset defect rather than a cosmetic one — the wiki
is a contract surface, not decoration.

## Why the separation matters

Each node answers a different question and produces a different artifact. Matching answers *should this
exist here*; generation answers *what does it say*; validation answers *does it hold*. A single fused
prompt returns one confident answer to all three, and the repository's own evidence shows what that
costs: a green static gate coexisting with a failed real-driver ablation and a quarantined asset.
