# Autoresearch Composer State Graph

## Purpose

This reference is Layer 3 for `skills/autoresearch_composer/skills.md`.
Load it when a task is a bounded metric-driven optimization loop.

## Nodes

| node | responsibility | output |
|---|---|---|
| S0 intake | preserve low-compression user context | context packet |
| S1 match | decide whether this is a real metric loop | route candidate or native yield |
| S2 route | choose `/autoresearch:<sub>` and native-yield decision | route decision |
| S3 generate | create Iteration-Loop Contract | contract block |
| S4 validate | check cases, A/B delta, metric, guard, and missing terms | PASS/FAIL |
| S5 recover | repair missing information or Domain terms | clarified context |

## Conditional Edges

- `conditional_edge.S1.no_numeric_metric`: yield to grilling or SDLC planning.
- `conditional_edge.S2.native_skill_better`: yield to diagnose, tdd, security review, or grilling.
- `conditional_edge.S3.missing_domain_term`: create `domain_terms` with known/candidate/unknown states.
- `conditional_edge.S3.compressed_context`: restore low-compression semantic truth before generation.
- `conditional_edge.S4.no_cases`: fail promotion until 10-20 behavior cases exist.
- `conditional_edge.S4.ablation_not_positive`: fail promotion until A/B delta passes.

## Contract Fields

`Goal`, `Scope`, `Metric`, `Direction`, `Verify`, `Guard`, `Iterations`,
`route`, `executor`, `domain_terms`, `known_unknowns`, and `human_gate` are
required for production promotion.

## Semantic Truth Actor Routing

Do not write `Opus or Codex or agy` as an unresolved actor choice. The route
must name one actor, the evidence it can see, and the output it is allowed to
produce.

| semantic question | actor | output | forbidden shortcut |
|---|---|---|---|
| Is the plan faithful to the original low-compression request? | Opus fresh judge | findings-only route surface with candidate / [推論] / human_required labels | no automatic admit |
| Do production repo scripts, cases, and A/B gates physically run? | Codex engineering audit plus T0 scripts | terminal evidence and implementation findings | no name-only technical_equivalent claim |
| Are agy/Gemini slash-command runtime facts true? | agy findings or external-verify | execution transcript or observed runtime evidence | no guessed external-runtime fact |
| Are Domain terms clear enough for a fresh LLM? | repo-agent-native or human glossary admit | domain_terms ledger with known/candidate/unknown states | no candidate/unknown term in hard-gate conclusion |

If evidence is missing, the state is candidate or [推論]. Promotion requires
validator output, ablation telemetry, or a human-admitted glossary artifact.
