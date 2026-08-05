---
type: Architecture
title: Evidence Cost
description: The cost cache and collector, their hash-bound request flow, the axis semantics that refuse to report a measured zero, and the unclosed v1/v2 handoff between them.
tags: [terminal-operator, measurement, cost]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [cost-measurement, content-addressed-cache, axis-semantics]
libraries: [bun]
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Evidence Cost

Two modules measure what producing a piece of evidence costs. Neither can grant admission, and — as
shipped — the collector's output cannot be fed back into the cache.

## End-to-end flow

```text
request  -> canonical content-addressed request (publish, then reopen)
         -> ready-stage progress
         -> execution (collector only, under an owned process-group carrier)
         -> axis-scoped observation receipt
         -> cache entry
         -> ledger
         -> completion receipt
```

Every arrow publishes through [writer publication](writer-publication.md), and every consumer *reopens*
what it was handed rather than trusting the value passed in.

## `evidence-cost-cache.ts` — a projector, not a runner

`evidence-cost-cache-cli.ts --request <request.json>` runs only after the referenced receipts exist. The
request must hash-bind every source input, the cross-cutting plan, the toolchain argv and version, the
stage evidence, the oracle relation, and a **separate** cost-observation receipt. Cost fields are
derived from the reopened observation and are never accepted directly from the request — the request
says which observation, not what it cost.

It publishes a canonical content-addressed request atomically, reopens it, and only then classifies a
cache hit. A cache entry is reused only after **all** its bindings are reopened.

### Cache identity is the stage plus its resolved sources

`cacheIdentity(request, stage, sources)` builds exactly two members — nothing else contributes to the
key:

```ts
{ stage,                                   // the whole EvidenceStage record
  trace: { plan: request.plan,             // the cross-cutting plan
           source_inputs: stage.source_input_ids.map(id => sources.get(id)) } }
```

Each `source_input_id` is **resolved to the full source record**, not carried as an id; an unresolvable
id throws `evidence-cost-source-missing:<id>` rather than producing a key over a dangling reference.
The key itself is `sha256(JSON.stringify({ stage, trace }))`, so two stages with identical definitions
and identical resolved sources collide deliberately — that collision *is* the cache.

Note what is **not** in the identity: the request id, the observation, the toolchain, and the cost
values. The key answers "same work over the same inputs", and the cost is looked up against it.

### `validateCacheEntry` — reopen before reuse

Every entry, whether found on disk or just published, is re-parsed and checked against six conditions.
Any failure rejects the entry:

- `exactKeys` — the entry has precisely
  `schema_version`, `cache_key`, `created_by_request_sha256`, `status`, `stage`, `trace`, and no extras;
- `schema_version === "repo-evidence-cost-cache-entry@v1"`;
- `cache_key` equals the key just computed;
- `status === "passed"`;
- `created_by_request_sha256` is SHA-256 shaped;
- `JSON.stringify({stage, trace})` is **deep-equal** to the identity — the entry must describe the same
  work, not merely hash to the same key.

### The three statuses, and how each is reached

`projectEvidenceCostCache()` reads the entry with `readAnchoredArtifact()`, then:

| Condition | Status |
|---|---|
| the key was already seen **within this request** (`seen.has(key)`) | `cross-combination-hit` — the entry must still exist on disk, else `evidence-cost-cache-reopen-missing:<stage_id>`; it is revalidated regardless |
| entry exists on disk, and its `created_by_request_sha256` **equals** this canonical request | `miss` |
| entry exists on disk, and its `created_by_request_sha256` **differs** | `persistent-hit` — an earlier request created it |
| no entry — publish, reopen, validate, then apply the same `created_by` comparison | usually `miss`, but `persistent-hit` when the reopened bytes came from a concurrent writer that won the race |

Two consequences are easy to get wrong:

- **`miss` does not mean "nothing was there"** — it means the entry on disk was created by *this*
  request. A request that re-derives the same stage twice records the first as `miss` and the second as
  `cross-combination-hit`.
- **The publish branch can still report `persistent-hit`.** `publishAndReopen()` inherits the writer's
  `matched-existing` semantics, so if another request published first, the reopened entry carries their
  request hash and is classified accordingly. The count reflects what is on disk, not what this process
  intended to write.

The completion receipt reports `unique_entries`, `misses`, `persistent_hits` and
`cross_combination_hits` separately, so a run that appears to hit heavily can be checked against
whether those hits were genuinely persistent or merely intra-request repeats.

`materializeStage()` accepts `repo-evidence-cost-observation@v1` with claim boundary
`external-hash-bound-observation/unadmitted-collector`. Such evidence is treated as **projected**: its
aggregate axes are named `asserted_*`, and it is routed to the trusted collector node rather than to
foreground admission.

This projector never runs a worker or grants admission. Its four-projector race probe belongs to opt-in
production mode, not the foreground edit loop.

## `evidence-cost-collector.ts` — the measuring one

`evidence-cost-collector-cli.ts --request <collector-request.json>` runs **only** with
`REPO_EVIDENCE_COLLECTOR_PRODUCTION=1`. Before measuring it reopens the plan, the source inputs, the
typed passing stage evidence, the canonical request, and ready-stage progress; then binds the committed
collector bundle (`EVIDENCE_COST_COLLECTOR_BUNDLE_REFS`), the resolved executable hash and version, the
Git HEAD, and the exact argv.

Execution uses an owned Bun process-group carrier. Every Git, runtime and stage subprocess has a hard
deadline and a cancellation path, and receipt finalization runs in a **separate bounded phase** so a
cancellation stays diagnosable rather than losing the reason.

It publishes content-addressed request, execution, axis-scoped observation and completion receipts.
Any timeout, cancellation, nonzero exit, stream/registry/sentinel leak, nested hash drift, or HEAD drift
**fails closed**. Precondition and system failures are persisted separately by
`persistEvidenceCostPreconditionFailure()`, so "it never started" is distinguishable from "it started
and broke".

## Axis semantics: never a measured zero

The v1 collector physically measures wall time and **direct-child CPU only**.

| Axis | Status |
|---|---|
| wall time | measured |
| CPU | measured, direct child only; unavailable CPU is an explicit failure, not zero |
| I/O | Bun operation counts are **diagnostic** and must not be relabelled as bytes |
| LLM tokens | `not-selected` until a trusted provider receipt exists |
| stage source closure | explicitly `not-selected` — collector HEAD proves its own bundle, not every file the stage may read |

Preserving `not-selected` / `not-applicable` instead of writing `0` is the point: a zero is a
measurement, and claiming one you did not take is the failure mode the whole module is built against.

While I/O bytes or LLM tokens are missing, `admission_eligible` stays **false** and the same collector
prompt is retained. Nothing this module produces can admit anything.

## The unclosed v1/v2 handoff

The cache accepts observation **`@v1`** with claim boundary
`external-hash-bound-observation/unadmitted-collector`. The collector emits observation **`@v2`** with
claim boundary `trusted-direct-process-collector/axis-scoped`.

**They do not compose.** Collector output cannot currently be fed back into the cache projector; the
schema and the claim boundary both differ. This is a known open seam, not a configuration mistake, and
any work that assumes a closed loop between the two will not find one.

The heavy collector profile is suitable for a later sealed background worker, but the dispatcher that
would run it remains disabled — see [Async lifecycle](async-lifecycle.md).

## Evidence boundary

All of the above is read from vendored source; nothing here has been executed in this checkout. See
[Terminal operator overview](overview.md).
