---
type: Reference
title: Plan Package Compatibility
description: PROJECT-SSOT.md as the project contract, plan-package.compat.yaml as its machine-readable surface, the provenance lock, and the gate that enforces the negative half.
tags: [governance, contract, provenance]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [project-contract, forbidden-paths, provenance-lock]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Plan Package Compatibility

This repository is the **final repo** output of the `unknown-discovery-gcr-order` plan package. Three
files describe that relationship at different levels of formality, and one gate enforces it.

| File | Role |
|---|---|
| `PROJECT-SSOT.md` | the contract in prose — archetype, ownership split, and the honesty rules |
| `plan-package.compat.yaml` | the same contract as machine-readable key/values |
| `.plan-package.lock.yaml` | the provenance lock |
| `scripts/check_plan_package_compat.py` | the gate |

## What the contract fixes

`PROJECT-SSOT.md` sets `project_archetype: skill-asset-governance-repo` and splits ownership:

> Prototype output owns the mirrored small-loop control plane, frozen source, lineage, invariants, and
> molecular commit plan. Final repo output owns skill assets, local/CI defense scripts, workflow hooks,
> provenance lock, and compatibility guards. **Final repo output must not contain small-loop control
> assets, exchange packets, or template drafts.**

The negative half is the enforced half. `check_plan_package_compat.py` — and
`scripts/check_openwiki.py` independently — fail if any of `small-loop`, `packets`, or
`templates/skill-defense-governance` exists at the repository root.
`plan-package.compat.yaml` records the same list as `final_repo_forbidden_paths`, plus
`final_repo_has_small_loop: false` and `final_repo_small_loop_policy: forbidden` in
`openwiki/openwiki.yaml`.

It also states the traceability requirement that
[Molecular commit lineage](molecular-commit-lineage.md) implements: *"Every production file must trace
to an intent slice, route, draft template, validator, and molecular commit."*

## The compatibility surface

`plan-package.compat.yaml` is worth reading as a status dashboard, not just a manifest. Its counts pin
the shape of the generated repo (`molecular_commit_count: 10`, `governance_template_count: 16`,
`project_template_file_count: 86`, `shadow_defense_count: 8`, `lineage_edge_count: 92`,
`plan_package_input_count: 39`), and `frozen_source_sha256` binds the frozen source conversation.

Its status fields are where the repository states what it has *not* achieved:

```yaml
p11_current_scope: local-zero-llm-regex-canary
synthetic_case_quality_status: insufficient
real_synthetic_generation_gate_required: true
semantic_arbitration_status: candidate_until_human_admit
autoresearch_cloud_judge_policy: implemented_disabled_by_default
adversarial_review_policy: codex-executed-agy-required-before-promotion
```

Each of those maps to a page: [synthetic corpus quality](../validation/synthetic-corpus-quality.md),
[semantic arbitration](../validation/semantic-arbitration.md),
[behavioral eval and judge](../validation/behavioral-eval-and-judge.md).

`autoresearch_cloud_judge_policy: implemented_disabled_by_default` deserves a caveat: the code path and
environment contract exist, but `cloud_judge()` makes no API call at all, and the workflow passes a
value the code does not accept. "Implemented" means wired, not functional.

## Multiple inputs

> Multiple inputs may update the same plan package only through `inputs/plan-package-inputs.yaml` plus
> explicit source-ingest packets.

`input_registry: inputs/plan-package-inputs.yaml` names the registry;
`plan_package_input_policy: registry-order-then-packet-state` fixes precedence. That registry lives in
the prototype half and is not present in this checkout, so the policy is documented here but not
verifiable here.

## Evidence origin

| Claim | Tier |
|---|---|
| forbidden paths absent, required files present, counts match | **verifiable in this checkout** — `python3 scripts/check_plan_package_compat.py` |
| the frozen-source SHA-256 corresponds to a real conversation | **receipt / data claim only** — the source file is outside this tree |
| input-registry precedence | **requires explicit external input** — the registry is in the prototype half |

## Validation

```sh
python3 scripts/check_plan_package_compat.py
bash scripts/test_plan_package_compat.sh
```

Neither is in `scripts/git_gate.py::GATES`; run them when the contract files change. See
[Entrypoint matrix](../operations/entrypoint-matrix.md).
