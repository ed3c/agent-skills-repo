# agent-skills-repo

This is the final repo generated from the `unknown-discovery-gcr-order` plan package.

## Delivery evidence authority

Issue comments, checkboxes, and project fields coordinate work; they do not prove delivery. The
repository-local authority is `data/project/landing-evidence.json`. A work item may be marked
`completed` only when `scripts/check_landing_evidence.py` verifies a full commit SHA reachable from
`main`, the exact changed-path digest, and the test-evidence digest.

The sandbox executor is **not currently reachable from `main`**. Its contract implementation and
OpenShell 0.0.59 adapter are under review, but the required real gateway-backed run has not been
executed. Issue #3 therefore remains open, and calibration or qualification must not claim
executor-backed evidence yet.

Validate the authority from a full-history checkout:

```sh
python3 scripts/check_landing_evidence.py --main-ref origin/main
```

## Sandbox executor contract

The in-review executor separates substrate execution from receipt production. It runs one
preregistered deterministic case in a fresh OpenShell sandbox, collects effective policy and Docker
image evidence, verifies sandbox/container destruction, and only then signs an existing
`sandbox-case-receipt@1` envelope.

Contract validation is local and credential-free:

```sh
python -m pytest -q tests/test_sandbox_executor.py
python -m compileall -q skill_arena scripts
```

A real run additionally requires OpenShell `0.0.59`, a reachable Docker-backed gateway, and an
owner-only development Ed25519 key outside this repository. See
[`docs/sandbox-executor.md`](docs/sandbox-executor.md). The status authority explicitly remains
`real_integration: not_executed` until physical evidence is landed.

## Usage Entry

Use this repo as a skill-asset governance seed:

```sh
git config core.hooksPath .githooks
python3 scripts/validate_commit_message.py --selftest
python3 scripts/validate_molecular_commit_lineage.py --require-current-history
python3 scripts/git_gate.py
python3 scripts/eval_autoresearch_composer.py --dataset data/autoresearch_golden/pr_golden_set.json
python3 scripts/sample_autoresearch_traces.py
python3 scripts/check_wiki_graph_sync.py
python3 scripts/check_openwiki.py
python3 scripts/check_plan_package_compat.py
python3 scripts/check_landing_evidence.py --main-ref origin/main
python3 -m pytest -q tests/test_sandbox_executor.py
```

The primary human/agent guide is `openwiki/quickstart.md`, the wiki's single declared entry
(`openwiki_entry` in `openwiki/nonofficial/openwiki.yaml`). `openwiki/nonofficial/README.md` remains
the index of the hand-written pages, reachable from quickstart. The final repo contains
runtime assets and validation scripts only. Small-loop control assets stay in
`prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/`.

## What This Repo Owns

- skill asset: `skills/gemini_interactions/skills.md`
- behavior cases: `skills/gemini_interactions/cases.json`
- local defense entries: `.githooks/pre-push`, `.githooks/commit-msg`
- production gate entry: `scripts/git_gate.py`
- landing-evidence authority: `data/project/landing-evidence.json`
- landing-evidence validator: `scripts/check_landing_evidence.py`
- sandbox executor package: `skill_arena/sandbox_executor/`
- OpenShell execution entry: `scripts/run_sandbox_case.py`
- in-sandbox runner: `scripts/sandbox_case_runner.py`
- executor profile and policy: `data/sandbox_profiles/`
- executor implementation status: `data/verification_runs/openshell_executor_status.json`
- compensated molecular commit lineage: `data/commit_lineage/gcr_molecular_commits.json`
  (the ledger and its validator live here; the commits it describes do not. It is therefore
  **not** in `git_gate.py`'s gate list — validate it from the workspace that holds those
  commits: `python3 scripts/validate_molecular_commit_lineage.py --repo-root <workspace>
  --audit-protected-history`. See the note in `scripts/git_gate.py`.)
- protected-history verification run: `data/verification_runs/gcr_three_surface_commit_traceability_2026-07-27.json`
- wiki graph sync entry: `.github/workflows/wiki_graph_sync.yml`, `scripts/sync_wiki_to_graph.py`
- openwiki entry: `openwiki/quickstart.md`
- plan compatibility lock: `.plan-package.lock.yaml`

## What This Repo Does Not Own

- plan packets;
- small-loop routes;
- template drafts;
- antigravity `kb-ingest` or KG ingestion;
- a production signing key or production trust root;
- a completed real OpenShell executor receipt until issue #3's physical integration gate passes.
