---
type: Reference
title: OpenWiki Index
description: Navigation index for the agent-skills-repo wiki — the contract entry point, listing every major section and what each one owns.
tags: [index, navigation]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [wiki-navigation]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# OpenWiki — agent-skills-repo

This repository is a skill-asset governance seed: it owns skill assets, the deterministic scripts that
defend them, the structured evidence those scripts read, and a local-first projection of this wiki into
a retrieval graph.

Two entry points, deliberately:

- **This page** is the human navigation index and the entry named by `openwiki/openwiki.yaml`
  (`openwiki_entry`) and required by `scripts/check_openwiki.py`.
- **[Quickstart](../quickstart.md)** is the agent entry point. It carries the task-routing table that maps
  a change area to its page, source entrypoints, tests, and minimal validation command. Start there if
  you are about to change something.

## Usage Entry

Start with [Usage](usage.md) for the command runbook, or run the whole chain directly:

```sh
git config core.hooksPath .githooks
python3 scripts/git_gate.py
```

The repository-root `README.md` carries the same entry commands for someone who has not opened the wiki
yet.

## Sections

### Lifecycle and workflow

- **[Asset Lifecycle Map](asset-lifecycle-map.md)** — the five phases a skill asset passes through
  (compile, hook, CI, production, weekly audit) and the strength of claim each one makes.
- **[stateful-workflow.md](stateful-workflow.md)** — the S1–S7 authoring state graph and its conditional
  edges; why matching, generation and validation stay separate nodes.
- **[Code Call Lifecycle](code-call-lifecycle.md)** — who calls what, from hooks and workflows down into
  each gate, with the values every gate currently reports.

### Assets and validation

- **[Skill asset contract](../skill-assets/contract.md)** — the `skills.md` / `cases.json` / `references/`
  shape and the baseline `scripts/validator.py` enforces.
- **[gemini_interactions](../skill-assets/gemini-interactions.md)** — quarantined asset; read this before
  routing it anywhere.
- **[autoresearch_composer](../skill-assets/autoresearch-composer.md)** — the state-graph asset and its
  routes.
- **[autoresearch-composer-lifecycle.md](autoresearch-composer-lifecycle.md)** — its executed golden
  dataset and ablation evidence, and the hardening it still lacks.
- **[Validation](../validation/)** — static validators, behavioral eval and judge, ablation and benchmark,
  synthetic corpus quality, semantic arbitration.

### Data, provenance and governance

- **[Structured Lifecycle Data](structured-lifecycle-data.md)** — generated display layer for
  `data/lifecycle/`. **Never hand-edit**: `scripts/check_lifecycle_datasets.py` requires it to be
  byte-equal to `scripts/render_lifecycle_openwiki.py --stdout`.
- **[prompt-trace-assets.md](prompt-trace-assets.md)** — the three prompt slots, the five required
  actors, and which parts of the gate need explicit external roots.
- **[Data authority](../architecture/data-authority.md)** — who owns which artifact and how each is
  regenerated.
- **[Structured lifecycle datasets](../lifecycle/structured-datasets.md)** — the `data/lifecycle/` SSOT and
  every cross-consistency rule its gate enforces, including the human-admit binding.
- **[Governance](../governance/)** — molecular commit lineage and plan-package compatibility.

### Wiki to graph

- **[wiki-graph-sync-architecture.md](wiki-graph-sync-architecture.md)** — local-first Event Sourcing,
  overwrite semantics, and the two-part guard on external graph writes.
- **[schema-standards.md](schema-standards.md)** — the node, event, embedding and license-provenance
  contracts.

### Runtime and operations

- **[Defense gate chain](../architecture/defense-gate-chain.md)** — `git_gate.py` internals and its
  input-state guarantee.
- **[Entrypoint matrix](../operations/entrypoint-matrix.md)** — every hook and workflow, its exact argv and
  environment format, and the scripts nothing triggers.
- **[Terminal operator](../terminal-operator/overview.md)** — the vendored Bun/TypeScript operator and why
  it cannot run from this checkout.
- **[Test map](../testing/test-map.md)** — what each pytest module proves.

### Provenance

- **[Non-official provenance map](provenance.md)** — which artifacts come from OpenWiki's own
  design, which are pinned at fixed paths by this repository's gates, and which are skill-bettor port
  extensions. Read it before adding or moving anything in this directory.

### Limits

- **[production-bottlenecks.md](production-bottlenecks.md)** — what the green gates do not prove, the
  root-local-runtime pinning, and the tracked documentation debt. Read this before quoting any number
  from this wiki as a capability claim.
