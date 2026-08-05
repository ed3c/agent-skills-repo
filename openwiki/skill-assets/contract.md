---
type: Reference
title: Skill Asset Contract
description: The shape of a skill asset in this repository and the exact baseline scripts/validator.py enforces on its behavior cases.
tags: [skill-assets, contract]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [skill-asset-shape, case-baseline, progressive-disclosure]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Skill Asset Contract

```text
skills/<slug>/
  skills.md          router: WHY / HOW / WHEN / WHEN NOT
  cases.json         behavior cases, 10-20 of them
  status.json        promotion decision (gemini_interactions only, today)
  references/        material deliberately held back from the router
```

## `skills.md` — four sections, nothing else

The router is four labelled sections and is meant to stay dense:

| Section | Answers |
|---|---|
| `WHY:` | what problem the asset exists to prevent |
| `HOW:` | the mechanism, including which reference to load and when |
| `WHEN:` | the trigger condition |
| `WHEN NOT:` | the adjacent tasks it must decline |

`WHEN NOT` carries as much weight as `WHEN`. `skills/gemini_interactions/skills.md` declines Angular
components, Vue components, static data extraction and unrelated cloud deployment;
`skills/autoresearch_composer/skills.md` declines general SDLC planning, TDD fixes, security reviews,
bug diagnosis, design grilling and family eval-case authoring. An asset that never declines cannot be
measured by its negative cases.

`scripts/skill_description_linter.py` checks the description stays a dense route boundary;
`scripts/validate_goal_constraints.py` requires goal/constraint phrasing rather than brittle step lists.

## `references/` — progressive disclosure

Detail that a router does not need lives under `references/` and is loaded only on demand —
`references/deploy_guide.md` for deployment specifics, `references/state_graph.md` for domain-term
recovery. `scripts/validate_progressive_disclosure.py` enforces the boundary. The point is context
economy: everything in `skills.md` is paid for on every routing decision.

## `cases.json` — the enforced baseline

`scripts/validator.py::validate_cases()` is the contract. Each requirement below is a hard failure, not
a warning:

| Rule | Detail |
|---|---|
| file shape | valid UTF-8, JSON array at the root |
| count | **10–20 cases** |
| required fields | `id`, `prompt`, `should_trigger`, `expected_checks` on every case |
| `prompt` | non-empty string |
| `should_trigger` | boolean, not a truthy string |
| `expected_checks` | non-empty list of strings, none of them in `WEAK_PATTERNS` = `{"", ".*", ".+", "^.*$", "^.+$"}` |
| polarity | at least 5 positive **and** at least 5 negative cases |
| distinctness | no two prompts above a `SequenceMatcher` ratio of **0.85** |

Two of these encode a lesson rather than a preference. The weak-pattern rejection stops a case from
passing by matching anything, which is how a corpus silently becomes decorative. The 0.85 near-duplicate
check stops template inflation — the exact failure `scripts/synthetic_case_quality_report.py` measures
elsewhere at a `0.9969` similarity ratio; see
[Synthetic corpus quality](../validation/synthetic-corpus-quality.md).

`expected_checks` entries are regexes, and `scripts/ablation_engine.py::regex_pass()` gives one prefix
special meaning: a check beginning `FORBID:` inverts, failing the case if the pattern **is** present.

`scripts/validate_skills_baseline.py` wraps the same validator with git awareness — it validates only
`skills/*/cases.json` files reported modified by `git status --porcelain`, falling back to all of them
when nothing is modified or the directory is not a Git work tree.

## `status.json` — the promotion decision

`skill-promotion-status@0.1.0`, carrying `status`, `production_routable`, `decision_date`, a
`verification_receipt` path, itemised `reasons`, and a `next_action`. It is written by a human after
reading a receipt.

**No gate reads this file.** It records a decision; it does not enforce one. See
[gemini_interactions](gemini-interactions.md) for the live instance.

## Adding a skill

1. Create `skills/<slug>/skills.md` with the four sections; keep detail in `references/`.
2. Write `skills/<slug>/cases.json` to the baseline above — start from the negative cases, since they
   are the half that is easy to get wrong.
3. `python3 scripts/validator.py` until green, then `python3 scripts/git_gate.py`.
4. Measure a delta: `python3 scripts/ablation_engine.py --cases skills/<slug>/cases.json`. The
   simulator in `ablation_engine.py::simulate_agent()` branches per `skill_slug`, so a new slug needs a
   branch there or it will not be simulated meaningfully.
5. A real behavioral claim requires `scripts/real_driver_ablation.py`, not the simulator. See
   [Ablation and benchmark](../validation/ablation-and-benchmark.md).

## Validation

```sh
python3 scripts/validator.py
python3 scripts/validate_skills_baseline.py --all
```
