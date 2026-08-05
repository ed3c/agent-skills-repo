# agent-skills-repo

This is the final repo generated from the `unknown-discovery-gcr-order` plan package.

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
- antigravity `kb-ingest` or KG ingestion.
