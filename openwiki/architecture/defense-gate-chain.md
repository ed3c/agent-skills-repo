---
type: Architecture
title: Defense Gate Chain
description: How scripts/git_gate.py sequences 23 gates, proves it did not mutate the repository, and writes a tamper-evident receipt.
tags: [gates, runtime]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [gate-orchestration, input-state-hash, gate-receipt]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Defense Gate Chain

`scripts/git_gate.py` is the single orchestrator. `.githooks/pre-push` and
`.github/workflows/skill_ci.yml` both invoke it and nothing else.

## The 23 gates, in order

`GATES` is a literal list, executed top to bottom:

```text
validator.py · validate_skills_baseline.py · skill_description_linter.py
validate_progressive_disclosure.py · validate_goal_constraints.py · validate_commit_message.py
github_skill_harvester.py · synthetic_case_generator.py · synthetic_case_quality_report.py
semantic_arbitration_report.py · interactions_patch_assert_runner.py · local_regex_runner.py
benchmark_runner.py · ablation_engine.py · llm_judge.py
check_openwiki.py · check_wiki_graph_sync.py · render_lifecycle_openwiki.py
check_lifecycle_datasets.py · check_autoresearch_lifecycle.py
validate_molecular_commit_lineage.py · no_op_pruner.py · no_ops_purger.py
```

Each runs as `subprocess.run([sys.executable, root/gate], cwd=root)`. **Execution stops at the first
nonzero exit** — later gates simply do not run, so a failure report is a prefix of the chain, not a
full survey. Reading a failure as "only one thing is wrong" is a mistake; run again after fixing.

Ordering is deliberate: the cheap static asset checks come first, the documentation and evidence gates
last, so the common failure surfaces fastest.

## Gates must not mutate the repository

`input_state_sha256(root)` walks every file and symlink under the root — excluding `.git`,
`__pycache__`, `.pytest_cache`, `.DS_Store` and `*.pyc` — and folds relative path, permission bits,
and either the symlink target or the file bytes into one SHA-256. It runs **before and after** the
whole chain.

If the two hashes differ, the run exits **125** and prints
`FAIL: git gate changed receipt-bound repo inputs`, regardless of whether every gate passed. A gate
that writes a file is itself a defect. This is why `render_lifecycle_openwiki.py` appears in `GATES` in
its default (compare) mode rather than `--write`: writing is an author action, verifying is a gate
action.

## The receipt

```sh
python3 scripts/git_gate.py --receipt /tmp/git-gate-receipt.json
```

Schema `git-gate-receipt@0.1.0`, containing per-gate `command`, `exit_code`, `elapsed_ms`, full
`stdout` / `stderr` and their SHA-256 digests, plus `input_state_sha256`,
`final_input_state_sha256`, `gate_count`, `expected_gate_count`, `repo_root`, and a `status` that is
`passed` only when the exit code is zero **and** every gate ran **and** the input state was stable.
A short chain that stopped early can never report `passed`.

Two implementation details are load-bearing:

- **`--receipt` must live outside `--repo-root`.** The parser rejects a path inside it, because writing
  the receipt into the tree would change the very input-state hash the receipt attests to.
- **The write is atomic.** `write_receipt()` creates `.{name}.{pid}.tmp` with mode `x` (fails if it
  exists), chmods it `0600`, then `os.replace()`s it into place, so a concurrent reader never sees a
  partial receipt.

`GIT_GATE_FIXED_ELAPSED_MS` overrides all timings, which is what makes receipts byte-reproducible in
tests.

## What a green chain does and does not prove

Proves: the asset contract holds; the deterministic runners reproduce their expected telemetry; the
required wiki pages exist with their required literals; the lifecycle display matches its generator; no
gate mutated the tree.

Does not prove: that an agent behaves better with a skill loaded; that a real commit message was
validated (`validate_commit_message.py` runs argument-less here and exercises only its selftest); or
that the gates outside this list ran at all. See [Entrypoint matrix](../operations/entrypoint-matrix.md)
and [Production bottlenecks](../nonofficial/production-bottlenecks.md).

## Validation

```sh
python3 scripts/git_gate.py
python3 scripts/git_gate.py --receipt /tmp/receipt.json   # then inspect status and gate_count
```

Focused test: `tests/test_skill_asset_governance.py::test_static_defense_scripts_pass` runs each gate
independently, so it reports *every* failing gate rather than stopping at the first.
