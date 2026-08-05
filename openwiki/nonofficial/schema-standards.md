---
type: Schema
title: Schema Standards
description: The node, event, embedding and license-provenance contracts declared by data/wiki_graph/schema.json, and which field each gate actually enforces.
tags: [schema, provenance]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [graph-schema, event-schema, license-provenance]
libraries: []
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Schema Standards

`data/wiki_graph/schema.json` is the single declaration every projection artifact is validated against.
It carries two independent version strings, because the event contract and the graph contract move
separately:

| Key | Value |
|---|---|
| `schema_version` | `wiki-graph-schema@0.1.0` |
| `event_schema_version` | `wiki-event@0.1.0` |
| `hybrid_retrieval_contract` | `hybrid-retrieval-contract@0.1.0` |
| `external_graph_write_policy` | `disabled-by-default` |

## WikiEvent

The event contract, versioned `wiki-event@0.1.0`. `required_event_fields` enumerates what every
`wiki_page_indexed` record must carry:

`event_id`, `event_type`, `source_path`, `commit_sha`, `content_sha256`, `occurred_at`, `payload`.

`event_id` is derived, not allocated — `sha256(source_path:commit_sha:content_sha256)` truncated to 24
hex characters and prefixed `evt:`. `occurred_at` is the fixed `GENERATED_EVENT_TIMESTAMP` constant, so
it identifies the generator, not a moment. `payload` holds the title, the heading tree, outbound links,
the code-block count, and the license block.

## WikiDocument and WikiSection

The graph contract, versioned `wiki-graph-schema@0.1.0`. `required_graph_fields` is
`nodes`, `edges`, `chunks`, `retrieval` — a projection missing any of the four is invalid even if the
individual records are well formed.

| Node type | Identity | Carries |
|---|---|---|
| `WikiDocument` | `doc:<source_path>` | `title`, `source_path`, `commit_sha`, `content_sha256`, `schema_version`, `license` |
| `WikiSection` | `section:<source_path>:<index>` | `source_path`, `heading_path`, `level`, `content_sha256` |

Edges are `HAS_SECTION` (document to its sections), `LINKS_TO` (document to a raw href), and
`USES_SCHEMA` (document to the graph schema version). Every edge records `source_event_id` and
`commit_sha`, so any edge can be traced back to the event that produced it.

## EmbeddingMetadata

`default_embedding_model` declares the vector side:

| Field | Value |
|---|---|
| `embedding_model_id` | `local-hash-embedding@0.1.0` |
| `dimension` | `16` |
| `model_license` | `N/A-deterministic-local-hash-no-model-weights` |

Every chunk carries `embedding_model_id`, so a chunk written under one embedding model is
distinguishable from a later one. The model is a deterministic local hash with no downloaded weights —
which is why its license slot is explicitly not-applicable rather than blank.

## LicenseProvenance

`required_provenance_fields` is `code_license`, `model_license`, `data_license`, `commercial_use`,
`copyleft_risk` — five fields on every `WikiDocument`. Their defaults and the `W3.license_unknown`
disposition are documented in
[Wiki graph sync architecture](wiki-graph-sync-architecture.md#license-provenance-carried-on-every-node).

`commercial_license_allowlist` names the licenses considered safe for commercial reuse:
`MIT`, `Apache-2.0`, `BSD-2-Clause`, `BSD-3-Clause`, `ISC`. It is an allowlist for review, not a filter
the projector applies — a page declaring something else is still projected, with its declaration
recorded as written.

## What `check_wiki_graph_sync.py` actually checks

Read this before assuming a green gate means the graph contract is fully proven.

1. **Seven required files exist** — `.github/workflows/wiki_graph_sync.yml`,
   `openwiki/wiki-graph-sync-architecture.md`, `openwiki/schema-standards.md`,
   `scripts/sync_wiki_to_graph.py`, `data/wiki_graph/schema.json`,
   `data/wiki_graph/event_log.jsonl`, `data/wiki_graph/sample_graph.json`. Missing files short-circuit
   the run before anything else is checked.
2. **The workflow really passes what it claims** — literal check for `openwiki/**/*.md`,
   `python scripts/sync_wiki_to_graph.py`, `python scripts/check_wiki_graph_sync.py`,
   `ENABLE_GRAPH_DB_WRITE`, `GRAPH_DB_URI`, `wiki-graph-local-projection`.
3. **The two wiki pages state the contract** — the architecture page must contain `Event Sourcing`,
   `Vector RAG`, `GraphRAG`, `LLM Wiki`, `W3.license_unknown`, `track | acceptable default`,
   `GRAPH_DB_KIND=generic-http-json`, `GRAPH_DB_KIND=neo4j-http`; this page must contain
   `WikiDocument`, `WikiEvent`, `EmbeddingMetadata`, `LicenseProvenance`, `Apache-2.0`.
4. **A fresh projection reproduces** — it re-runs `sync_wiki_to_graph.py` into a temporary directory
   with `--commit-sha check-local` and requires exit 0 with `PASS: wiki graph sync` in stdout.
5. **The sync script still contains its external-writer code** — literal check for `urllib.request`,
   `GRAPH_DB_KIND`, `generic-http-json`, `neo4j-http`, `post_neo4j_graph_payload`. Deleting a transport
   is caught even though no transport is exercised.
6. **The missing-secret path fails closed** — it re-runs the script with `ENABLE_GRAPH_DB_WRITE=true`,
   `--write-external-graph`, and `GRAPH_DB_URI` / `GRAPH_DB_USER` / `GRAPH_DB_PASSWORD` **removed from
   the environment**, then requires a non-zero exit whose output contains `missing secrets`.
7. **Field-by-field validation of the checked-in artifacts**, driven by the schema itself rather than a
   hardcoded list:

| Target | Rule |
|---|---|
| `schema.schema_version` | must equal `wiki-graph-schema@0.1.0` |
| `event_log.jsonl` | must contain **at least one** event |
| every event | must contain every name in `schema.required_event_fields` — `event_id`, `event_type`, `source_path`, `commit_sha`, `content_sha256`, `occurred_at`, `payload` |
| every event's `payload.license` | must contain every name in `schema.required_provenance_fields` — `code_license`, `model_license`, `data_license`, `commercial_use`, `copyleft_risk` |
| `sample_graph.json` | must contain every name in `schema.required_graph_fields` — `nodes`, `edges`, `chunks`, `retrieval` |
| `retrieval.vector_rag.enabled` | must be truthy — *"Vector RAG contract must be enabled"* |
| `retrieval.graphrag.enabled` | must be truthy — *"GraphRAG contract must be enabled"* |
| `retrieval.graphrag.external_write_enabled` | must be **exactly `False`** — *"external graph write must be disabled by default"* |
| `graph.chunks` | must be non-empty — *"graph must include chunks for Vector RAG"* |
| `graph.edges` | must be non-empty — *"graph must include edges for GraphRAG"* |

Because the field lists are read from `schema.json` at check time, adding a name to
`required_event_fields` immediately tightens the gate — the schema is the contract, not a copy of it.
Note the asymmetry in the `retrieval` rules: the two `enabled` flags are checked for truthiness, while
`external_write_enabled` is compared with `is not False`, so a missing or `None` value fails rather
than passing as falsy.

**What it does not check.** There is no integration test that a *successful* external graph write
happened. Both transports are exercised only through their argument construction and their failure
paths; no live Neo4j or HTTP endpoint is contacted. A green gate says the projection is reproducible
and the opt-in path fails safely — not that writing to a real graph database works.

| Enforcement | Where |
|---|---|
| the field lists above | consumed by `scripts/sync_wiki_to_graph.py` at projection time |
| everything in the numbered list | `scripts/check_wiki_graph_sync.py` |
| page existence, from the other direction | `tests/test_skill_asset_governance.py::test_skill_asset_structure` |

## Validation

```sh
python3 scripts/check_wiki_graph_sync.py
```
