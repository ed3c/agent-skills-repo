# agent-skills-repo

Evidence-first governance, qualification, and comparative-evaluation research for Agent Skills.

## Current status

This repository is **not yet a production SKILL.md Arena** and does not currently claim a qualified or ranked public catalog.

What exists on reachable `main` after this change:

- ordered local governance gates and commit hooks;
- deterministic source-anchor verification under `anchor_oracle/`;
- signed evidence, admission, hard-gate, lifecycle, and economics contracts under `skill_arena/`;
- a repository-local landing-evidence authority that binds completion to Git history, paths, and test evidence;
- deterministic canonical Agent Skills exports under `dist/agent-skills/`;
- a fail-closed OpenShell 0.0.59 sandbox-executor contract, CLI adapter, signed evidence-bundle writer, and contract tests;
- a quarantined legacy asset at `skills/gemini_interactions/`;
- a production-seed candidate at `skills/autoresearch_composer/`, still non-routable pending human admission;
- a native source-verified repo-wiki candidate at `skills/repo_wiki_verified/`, still marked `pending-qualification`;
- public corpus and blind-pool leakage guards;
- generated and hand-authored OpenWiki documentation.

What is being built next:

- a real OpenShell gateway-backed execution-evidence landing for the sandbox contract (#3);
- SkillsBench/BenchFlow task and execution adapters (#14–#15);
- sealed benchmark governance, statistical eligibility, supply-chain trust, and leaderboard publication (#16–#19).

The comparative Arena roadmap and interim dashboard are tracked in [issue #11](https://github.com/ed3c/agent-skills-repo/issues/11).

## Start here

- Human/agent operating guide: [`openwiki/quickstart.md`](openwiki/quickstart.md)
- Qualification pipeline: [`openwiki/qualification-pipeline.md`](openwiki/qualification-pipeline.md)
- Sandbox executor contract and physical-evidence boundary: [`docs/sandbox-executor.md`](docs/sandbox-executor.md)
- Arena feasibility study and architecture: [`docs/research/skill-arena-feasibility-and-roadmap.md`](docs/research/skill-arena-feasibility-and-roadmap.md)
- Arena local provider policy and capability boundary: [`docs/arena-provider-policy.md`](docs/arena-provider-policy.md)
- Quote-repair signed preregistration and execution boundary: [`docs/quote-repair-preregistration.md`](docs/quote-repair-preregistration.md)
- Agent Skills portability contract: [`docs/agent-skills-portability.md`](docs/agent-skills-portability.md)
- Portable skill registry: [`dist/agent-skills/registry.json`](dist/agent-skills/registry.json)
- Machine-readable roadmap: [`data/project/skill-arena-roadmap.json`](data/project/skill-arena-roadmap.json)
- Arena roadmap contract: [`contracts/skill-arena-roadmap.schema.json`](contracts/skill-arena-roadmap.schema.json)
- Delivery evidence authority: [`data/project/landing-evidence.json`](data/project/landing-evidence.json)
- Delivery evidence schema: [`contracts/landing-evidence.schema.json`](contracts/landing-evidence.schema.json)

## Usage Entry

Run the local governance, roadmap, delivery-evidence, portability, and executor-contract checks from the repository root:

```sh
git config core.hooksPath .githooks

python3 scripts/validate_commit_message.py --selftest
python3 scripts/validate_molecular_commit_lineage.py --require-current-history
python3 scripts/git_gate.py
python3 scripts/check_skill_arena_roadmap.py
python3 scripts/check_landing_evidence.py --main-ref origin/main
python3 scripts/export_agent_skills.py --check

python3 scripts/eval_autoresearch_composer.py \
  --dataset data/autoresearch_golden/pr_golden_set.json
python3 scripts/sample_autoresearch_traces.py
python3 scripts/check_wiki_graph_sync.py
python3 scripts/check_openwiki.py
python3 scripts/check_plan_package_compat.py
python3 scripts/run_sandbox_case.py --help

python3 -m pytest -q tests/test_skill_arena_roadmap.py
python3 -m pytest -q tests/test_landing_evidence.py
python3 -m pytest -q tests/test_agent_skills_export.py
python3 -m pytest -q tests/test_sandbox_executor.py
```

The roadmap checker remains separate from `scripts/git_gate.py` so the historical production-gate receipt contract is not silently changed. Delivery completion is governed by the full-history landing-evidence workflow and repository-local authority.

## Portable Agent Skills exports

Repository-native skill sources remain under `skills/<source_id>/skills.md`. Canonical external artifacts are generated under `dist/agent-skills/<hyphenated-name>/SKILL.md` with Agent Skills YAML frontmatter, byte-preserved behavior content, lifecycle metadata, and reproducible digests.

```sh
python3 scripts/export_agent_skills.py --write  # intentional regeneration
python3 scripts/export_agent_skills.py --check  # CI/staleness/conformance gate
```

The upstream Apache-2.0 `skills-ref` validator is pinned by full commit SHA in `data/agent-skills/export-policy.json`. Format conformance never changes qualification state: production-seed candidates, quarantined skills, and pending-qualification skills remain non-routable until their independent lifecycle authority permits routing.

## Sandbox executor boundary

`skill_arena/sandbox_executor/` implements the fail-closed contract and the concrete OpenShell 0.0.59 CLI adapter. `scripts/run_sandbox_case.py` accepts a preregistered case, profile, policy, external development key, and output directory; it publishes a signed evidence bundle only after deterministic result checks and cleanup verification pass.

The committed CI is contract evidence only. `data/verification_runs/openshell_executor_status.json` deliberately remains:

```text
real_integration: not_executed
qualification_eligible: false
```

A real gateway-backed run, a second fresh-workspace run, receipt admission, tamper rejection, backing-container destruction, and proof that the development private key is absent from repository history must be landed before issue #3 can close. Reported results from another workspace are provenance to recover, not a substitute for repository-local evidence.

## Product boundaries

### Qualification

Qualification asks whether one immutable skill artifact is admissible for one declared host, policy, sandbox, task-pool, and threshold envelope. It produces signed evidence and an explicit lifecycle decision.

The qualification epic is [#1](https://github.com/ed3c/agent-skills-repo/issues/1).

### Arena

Arena evaluation compares candidate skills with a no-skill baseline on the same tasks and pinned execution profiles. It reports paired lift, uncertainty, cost, latency, reliability, routing, safety, and compatibility. Arena rank must not silently change qualification status.

The Arena epic is [#11](https://github.com/ed3c/agent-skills-repo/issues/11).

## What this repository owns

- repository-native skill sources and lifecycle state under `skills/`;
- canonical portable Agent Skills exports and registry under `dist/agent-skills/`;
- deterministic export logic under `skill_arena/agent_skills_export.py` and `scripts/export_agent_skills.py`;
- deterministic skill-asset and corpus guards under `skill_arena/skill_assets.py`;
- admission, evidence, signature, gate, and qualification contracts under `skill_arena/` and `contracts/`;
- the sandbox execution contract under `skill_arena/sandbox_executor/`;
- the pinned OpenShell profile, smoke case, policy, status record, and CLI under `data/sandbox_*`, `data/verification_runs/`, and `scripts/run_sandbox_case.py`;
- deterministic source-anchor verification under `anchor_oracle/`;
- repository-local completion evidence under `data/project/landing-evidence.json`;
- completion-evidence validation under `skill_arena/landing_evidence.py` and `scripts/check_landing_evidence.py`;
- local defense entries `.githooks/pre-push` and `.githooks/commit-msg`;
- production gate entry `scripts/git_gate.py`;
- OpenWiki entry `openwiki/quickstart.md` and graph projection tooling;
- plan compatibility lock `.plan-package.lock.yaml`;
- Arena research, issue roadmap, and machine-readable delivery projection.

The compensated molecular commit-lineage ledger remains at `data/commit_lineage/gcr_molecular_commits.json`. The commits it describes are not all owned by this repository, so its highest-confidence validation must run from the workspace that holds those commits. See the note in `scripts/git_gate.py`.

## Evidence rule

An issue comment, checkbox, local test transcript, or short commit identifier is coordination information, not delivery proof.

A completed roadmap item must bind:

- a full commit reachable from `main`;
- the exact changed paths derived from that commit against its first parent;
- a digest of those paths;
- digested test or receipt evidence;
- independent verification state where required.

`data/project/landing-evidence.json` is the delivery authority. GitHub issues, pull requests, Projects v2, and human dashboards are projections of that evidence, not substitutes for it.

The sandbox executor contract is reviewable, but physical OpenShell execution evidence is not yet repository-local. Issue #3 therefore remains open, and calibration or qualification must not claim executor-backed evidence until the real-integration bundle lands and passes the repository-local authority gate.

## What this repository does not own

- internal plan packets and small-loop routes;
- template drafts outside the exported repository;
- Antigravity `kb-ingest` or external knowledge-graph ingestion;
- SkillsBench or BenchFlow internals;
- a production trust root or hosted multi-tenant execution service;
- a marketplace, payment system, or universal single-score leaderboard.

## License

MIT. Imported tasks, skills, runtimes, and dependencies retain their own licenses and must pass the permissive-license policy proposed in #18.
