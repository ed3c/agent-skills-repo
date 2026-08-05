---
type: Playbook
title: Usage
description: The local command runbook — what to run, in what order, what each command actually proves, and the narrowest check for each change area.
tags: [runbook, operations]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [local-runbook, narrow-validation, receipts]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Usage

Requires Python ≥ 3.11 (`pyproject.toml`). No third-party runtime dependency; `pytest` is needed only
for the test suite.

## First run in a fresh clone

```sh
git config core.hooksPath .githooks      # opt in to the local hooks; not enforced server-side
python3 scripts/git_gate.py              # the whole 23-gate chain
```

`git_gate.py` is also what `.githooks/pre-push` and `.github/workflows/skill_ci.yml` run, so a green
local run is the same evidence CI will produce.

## Narrow checks by change area

Run the whole chain before pushing, but while iterating use the smallest thing that fails if your change
is wrong.

| You changed | Run this | It proves |
|---|---|---|
| `skills/*/cases.json` | `python3 scripts/validator.py` | the 10–20 case baseline, polarity, non-weak checks, no near-duplicate prompts |
| `skills/*/skills.md` | `python3 scripts/skill_description_linter.py` and `python3 scripts/validate_progressive_disclosure.py` | dense routing boundary, reference material stays behind `references/` |
| an `openwiki/` page | `python3 scripts/check_openwiki.py` | required pages exist and carry their required literals |
| an `openwiki/` page (graph side) | `python3 scripts/sync_wiki_to_graph.py` then `python3 scripts/check_wiki_graph_sync.py` | the projection regenerates and matches the schema contract |
| `data/lifecycle/**` | `python3 scripts/render_lifecycle_openwiki.py --write` then `python3 scripts/check_lifecycle_datasets.py` | the display page is regenerated from the structured SSOT and matches it byte for byte |
| `data/autoresearch_golden/**` or the composer asset | `python3 scripts/check_autoresearch_lifecycle.py` | schema, cross-repo case identity, and the A/B ablation hard gate |
| `data/prompt_trace/**` | `python3 scripts/check_prompt_trace_assets.py` | the three prompt slots and five required actors |
| `scripts/**` | `python3 scripts/git_gate.py` | everything, because a script can be a gate |
| a commit message | `python3 scripts/validate_commit_message.py <file>` | the 11 required traceability fields |

Note the argument in the last row. Under `git_gate.py`, `validate_commit_message.py` and
`validate_goal_constraints.py` run with **no arguments**, which exercises only their selftest path — see
[Production bottlenecks](production-bottlenecks.md).

## Conditional and expensive checks

These are not part of the default chain. Run them when the corresponding condition holds.

| Command | Run it when |
|---|---|
| `python3 -m pytest -q` | before a release, or after touching `scripts/` broadly; it re-runs every static gate as a subprocess |
| `python3 -m pytest -q -m evals` | golden-dataset or trace assets changed |
| `python3 scripts/check_plan_package_compat.py` | `PROJECT-SSOT.md`, `plan-package.compat.yaml`, or `.plan-package.lock.yaml` changed |
| `python3 scripts/real_driver_ablation.py --agent-cmd '<cmd with {task}>'` | you are about to claim a real behavioral delta; this spends real agent calls and is the only evidence that can support promotion |
| `python3 scripts/validate_molecular_commit_lineage.py --require-current-history` | auditing commit history; **needs a discoverable Git root**, and this directory has none of its own |

## Receipts

`git_gate.py` can persist a machine-readable receipt:

```sh
python3 scripts/git_gate.py --receipt /tmp/git-gate-receipt.json
```

The receipt is `git-gate-receipt@0.1.0` and records per-gate exit codes, elapsed time, and SHA-256 of
each gate's stdout and stderr, plus the repository input-state hash taken before and after the run.
`--receipt` must point **outside** `--repo-root`; the script rejects a path inside it, because writing
the receipt into the tree would change the input-state hash it is attesting to. If any gate mutates a
tracked input, the run exits 125 regardless of gate results.

## Where to read next

- What each phase can and cannot prove → [Asset lifecycle map](asset-lifecycle-map.md)
- Who calls what, with measured values → [Code call lifecycle](code-call-lifecycle.md)
- Full trigger table including uncovered scripts → [Entrypoint matrix](../operations/entrypoint-matrix.md)
- Known limits → [Production bottlenecks](production-bottlenecks.md)
