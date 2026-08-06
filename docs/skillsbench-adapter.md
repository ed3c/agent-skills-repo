# SkillsBench adapter v0.1

The Arena consumes SkillsBench through a pinned adapter, not by treating a mutable branch or a fork as the product contract.

## Pinned upstream envelope

- repository: `benchflow-ai/skillsbench`
- commit: `9a1f4dd5f7659f75707435da3ce854b6e48321d1`
- license: Apache-2.0
- task schema: native `task.md` `1.3`
- execution compatibility probe: `benchflow==0.6.3`

The selected tasks are:

| Task | Arena role | Task-local skills |
|---|---|---|
| `dialogue-parser` | code | `dialogue-graph` |
| `weighted-gdp-calc` | document/data | `xlsx` |
| `pdf-excel-diff` | skill composition | `pdf`, `xlsx` |

## Import contract

The importer requires a clean checkout at the exact commit. It rejects mutable refs, unapproved licenses, unknown top-level task fields, symlinks, special files, path escapes, task/network drift, and task-local skill drift.

Every selected task becomes:

```text
<output>/<task-id>/sha256-<bundle-digest>/
  bundle.json
  parity.json
  package/       # byte-identical selected upstream package
```

`bundle.json` binds the upstream repository, full commit, license-file digest, task path, prompt/task digests, complete file list, executable bits, sandbox network policy, and skill-injection boundary. The directory name is the canonical bundle digest.

## Parity states

- `known_loss`: structural/package parity is exact, but oracle and same-verifier-probe execution evidence is absent.
- `equivalent`: structural parity is exact; both source and normalized oracle runs receive reward `1.0`; the same verifier probe input produces the same reward and diagnostic class.
- `rejected`: package bytes, execution reward, probe input, verifier reward, or diagnostics differ.

Structural equivalence alone is never ranking evidence. Initial CI imports all three real upstream tasks, validates their source and normalized packages with `bench tasks check`, and publishes the generated bundles as a workflow artifact. A later execution slice must bind oracle/verifier evidence before any row becomes ranking-eligible.

## Commands

```sh
python3 scripts/import_skillsbench_tasks.py import \
  --upstream-root /path/to/pinned/skillsbench \
  --output-root /tmp/arena-task-bundles

python3 scripts/import_skillsbench_tasks.py validate-all \
  --output-root /tmp/arena-task-bundles
```
