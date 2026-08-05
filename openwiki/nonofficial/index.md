# Files

- [OpenWiki Index](README.md) - Navigation index for the agent-skills-repo wiki — the contract entry point, listing every major section and what each one owns.
- [Asset Lifecycle Map](asset-lifecycle-map.md) - The five phases a skill asset passes through in agent-skills-repo, which script owns each phase, and what each phase can and cannot prove.
- [Autoresearch-Composer Lifecycle](autoresearch-composer-lifecycle.md) - What the autoresearch_composer asset optimizes, the executed golden-dataset and ablation evidence behind its production gate, and the hardening it still lacks.
- [Code Call Lifecycle](code-call-lifecycle.md) - The call graph from git hooks and CI workflows through git_gate into each defense script, with the measured values each gate currently reports and the one stale validator expectation.
- [Production Bottlenecks](production-bottlenecks.md) - The known limits of this repository's evidence — what the green gates do not prove, where the runtime is pinned to one machine, and the documentation debt that is tracked rather than hidden.
- [Prompt Trace Assets](prompt-trace-assets.md) - The prompt-trace dataset and golden eval, the three prompt slots they record, the actors required for selection, and exactly which parts of the gate are re-verified only with explicit external roots.
- [Non-official provenance map](provenance.md) - Why this directory exists — which artifacts come from OpenWiki's own design and stay at the wiki root, and which exist only because agent-skills-repo's gates pin them, and therefore live here.
- [Schema Standards](schema-standards.md) - The node, event, embedding and license-provenance contracts declared by data/wiki_graph/schema.json, and which field each gate actually enforces.
- [Stateful Workflow](stateful-workflow.md) - The S1–S7 state graph for authoring and admitting a skill asset, its conditional edges, and why matching, generation, and validation must stay separate nodes.
- [structured-lifecycle-data](structured-lifecycle-data.md)
- [Usage](usage.md) - The local command runbook — what to run, in what order, what each command actually proves, and the narrowest check for each change area.
- [Wiki Graph Sync Architecture](wiki-graph-sync-architecture.md) - Local-first Event Sourcing that projects the Markdown wiki into an event log and a hybrid retrieval graph, its overwrite semantics, and the two-part guard on external graph writes.
