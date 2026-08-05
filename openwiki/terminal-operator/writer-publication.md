---
type: Architecture
title: Writer Publication
description: publishWriterArtifact — the anchored, symlink-safe, crash-recoverable persistence core nine modules depend on, its exact outcome states, lock budget, recovery classification and failure taxonomy.
tags: [terminal-operator, persistence, atomicity]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [atomic-publish, crash-recovery, shared-persistence, anchored-descriptors]
libraries: [bun]
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Writer Publication

`writer-publication.ts` owns `publishWriterArtifact()`, the one function every other module uses to put
a byte on disk durably. A change here affects everything.

## Nine consumers

| Module | Publishes |
|---|---|
| `writer-publication.ts` | owner |
| `writer-entrypoint.ts` | the public writer seam, and the admission-relevant one |
| `async-worker-carrier.ts` | worker transaction state and results |
| `async-job-lifecycle.ts` | seals and CAS lifecycle events |
| `async-admission-facade.ts` | the immutable ownership receipt |
| `async-control-plane.ts` | projected queue and redrive refs |
| `async-progress-store.ts` | worker progress |
| `evidence-cost-cache.ts` | canonical requests and cache entries |
| `evidence-cost-collector.ts` | request, execution, observation and completion receipts |

The entrypoint and the worker are material boundaries in their own right — the first is the public
seam, the second publishes results from a subprocess-bearing transaction — and neither is "just another
async module".

## The anchored walk

`writer-native.ts::openWriterDirectory(root, parent)` never resolves a path twice. It:

1. `lstat`s the root and rejects it unless it is a real directory and not a symlink
   (`writer root must be a real directory`);
2. opens the root with `O_RDONLY | O_DIRECTORY | O_NOFOLLOW`;
3. `assertRootOpened()` — `fstat`s the descriptor and compares `dev`/`ino` against the pre-open `lstat`.
   A mismatch is `writer root changed before open`;
4. `walk()`s the relative parts from root to parent one component at a time through directory
   descriptors, collecting every descriptor for deterministic cleanup;
5. `parentIdentity()` — re-`lstat`s the parent path and `fstat`s the opened descriptor, requiring a real
   directory, not a symlink, with matching `dev`/`ino`. Otherwise
   `writer parent changed before anchored open`.

Because every step is descriptor-relative and `O_NOFOLLOW`, swapping a directory for a symlink midway
cannot redirect the write; it produces an identity mismatch instead.

`publishWriterArtifact()` accepts an optional `expectedParent` (`dev`/`ino`). When supplied and
mismatched, it closes the directory and throws an `AggregateError` combining
`[writer] failure_kind=parent-identity-mismatch` with any cleanup errors.

## The lock is bounded, and busy is a distinct outcome

`acquireWriterLock()` tries `flock(descriptor, LOCK_EX|LOCK_NB)` up to **3 times**, waiting
`attempt * 200 ms` between tries (200 ms, then 400 ms — a **600 ms total wait budget**). Only
`EWOULDBLOCK` (35 on darwin, 11 elsewhere) is retried; any other errno throws immediately as
`[writer] failure_kind=lock errno=<n>`. After three attempts it returns `false` rather than throwing.

A caller that failed to lock does not simply fail. `publishWithOwnership()` first checks whether the
artifact it wanted is *already there and identical*:

- if so, it returns that outcome with `recoveryOutcome: "none"` — a concurrent writer already did the
  work;
- otherwise it throws `[writer] failure_kind=busy retry_budget=3 wait_budget_ms=600`, and the budget is
  in the message so the diagnostic is self-describing.

## Outcome states

`publishLocked()` returns exactly one `writerOutcome`, paired with a `recoveryOutcome`:

| `writerOutcome` | When |
|---|---|
| `published` | the pending file was written, linked, fsynced, and read back equal |
| `matched-existing` | the output already existed with byte-identical content — idempotent success |
| *(throws)* `conflict` | the output exists with different content |

The conflict path is worth tracing: `link(pending, output)` returning `EEXIST` means someone published
between the existence check and the link. The code re-checks `existingWriterOutcome()`; if that now
matches, it returns the match, and if the output has vanished entirely it throws
`[writer] output disappeared after conflict` rather than retrying blindly.

`recoverPending()` classifies leftover state from an interrupted run by the pending file's link count:

| `recoveryOutcome` | Meaning |
|---|---|
| `none` | no pending file (`ENOENT`) |
| `pre-link` | pending exists with `nlink == 1` — the crash happened after the write and before the link; the content was never published |
| `post-link` | pending exists with `nlink == 2` — the crash happened after the link; the artifact **is** published and the pending name is residue |

Any other link count is `[writer] failure_kind=unsafe-recovery-pending`. On the `post-link` path the
output is reopened to confirm it is the same inode before the residue is cleared; a failure there is
`[writer] failure_kind=recovery-output errno=<n>`.

## Durability and readback

After a successful `link`, the sequence is fixed: mark `state.artifactCreated = true` → `fsync` the
**directory** descriptor (so the new name is durable, not just the bytes) → `assertStable()` →
`verifyPublished()`.

`verifyPublished()` reopens the output with `O_NOFOLLOW` and requires a regular file with
`nlink == 2` (`[writer] failure_kind=unsafe-published-link`) whose contents equal the candidate
(`[writer] failure_kind=publication-mismatch`). Publication is not believed until it has been read back.

Failure kinds are a closed set and each names its stage: `lock`, `busy`, `link`, `reopen`,
`unsafe-published-link`, `publication-mismatch`, `recovery-open`, `recovery-output`,
`unsafe-recovery-pending`, `parent-identity-mismatch`.

## `artifactCreated` — the "possibly published" diagnostic

Cleanup runs unconditionally in a `finally`: if the lock was held, the pending name is unlinked; then
every descriptor collected during the anchored walk is closed. Cleanup errors are **collected**, not
swallowed.

`publicationResult()` then decides the outcome:

- failures = `[primary, ...cleanupFailures]` (or just the cleanup failures when the primary succeeded);
- one failure is rethrown as-is inside a `WriterPublicationFailure`; several become an
  `AggregateError("writer publication and cleanup failed")`;
- **every** failure carries `artifactCreated`, so a caller can distinguish *"nothing was written"* from
  *"the artifact may exist but cleanup failed"*. That single boolean is the difference between safe
  retry and a state that must be inspected;
- a missing result with no failure is itself an error (`publication returned no result`) — the function
  never returns `undefined`.

Note the consequence: **a cleanup failure alone fails the publication**, matching `SKILL.md`'s rule
*"Treat cleanup failures as terminal failures, not log-only warnings."*

## Injected interruption points

`pauseWriterAt("pending-written")`, `pauseWriterAt("output-linked")` and `failWriterAt("output-linked")`
are deliberate seams so the two recovery classes above can be exercised on purpose rather than waited
for. They are what make `pre-link` and `post-link` testable states instead of theory.

## What the production journey asserts

`writer-production-race-scenario.ts::writerRaceScenario()` passes only when all four probes hold:

| Probe | Assertion |
|---|---|
| concurrency | `bounded_concurrency: 4` — four process-isolated writers race the same output; `deterministic_outputs` requires all four output hashes to collapse to **one**; `published_count` + `matched_existing_count` account for every run. `shared_mutation_observed: true` and `deterministic_receipts: false` are recorded honestly — the receipts differ by design, only the artifact must not |
| conflict | the conflicting writer exits **1** with a clean process and `receipt.failure_kind === "conflict"` |
| rollback | the **`post-link`** interruption — see below |
| cancellation | the **`pending-written`** interruption — see below |

### The two interruption points are distinct, and each asserts a different outcome

The scenario injects a kill at two different stages and requires the *matching* recovery class, so a
recovery that silently produced the wrong class fails even though nothing crashed.

| | rollback probe | cancellation probe |
|---|---|---|
| `injected_stage` | `post-link` — killed **after** the hard link | `pending-written` — killed **after** the pending file, **before** the link |
| kill process | clean stage | clean stage **and** `exitCode !== 0` |
| recovery process | exits `0` | exits `0` |
| recovery `writer_outcome` | must be **`matched-existing`** — the artifact was already published, so republishing is idempotent | *(not asserted; nothing was published)* |
| recovery `recovery_outcome` | must be **`post-link`** | must be **`pre-link`** |
| published artifact | `final_output_preserved` must be true — recovery must **not** disturb the already-published bytes | `final_output_absent_before_recovery` must be true — nothing may have been published |
| pending-file cleanup | `residue_count === 0` | `residue_count === 0` |

Both probes additionally record `process_reaped`, `stdout_consumed`, `stderr_consumed` (and
`timer_cleared` for cancellation), plus the recovered `artifact_id`, `run_id`, and — for rollback —
`output_sha256`, so the preserved bytes are identified rather than merely asserted to exist.

The asymmetry is the point: after `post-link` the correct behavior is *leave the artifact alone and
clear the residue*; after `pending-written` it is *publish nothing and clear the residue*. A recovery
that published on the cancellation path, or that rewrote the output on the rollback path, fails.

`writer-production-scenarios.ts::writerResourceScenario()` covers resource ownership across **12**
lifecycle runs, requiring every run to exit 0 with a clean process, plus two failure probes:

- timeout → `exitCode 124` with `timedOut` set;
- cancellation → `exitCode 130` with `cancelled` set;
- and for both, `processReaped && timerCleared && stdoutConsumed && stderrConsumed &&
  cleanupErrors.length === 0`.

A killed subprocess that leaves an unread stream or an uncleared timer fails the scenario even though
the write itself succeeded.

## Evidence boundary

All of the above is read from vendored source. **These scenarios cannot run here** — there is no Bun
toolchain, no `package.json`, and the admission-valid contained profile lives in an absent upstream
tree. See [Terminal operator overview](overview.md) and
[Production profiles and handoff](production-profiles-and-handoff.md).

## If you change this file

Every consumer above inherits the change. The narrowest meaningful check is the writer's own contract
and probe modules, then the production profile that exercises the real carrier — not a unit test of a
single caller.
