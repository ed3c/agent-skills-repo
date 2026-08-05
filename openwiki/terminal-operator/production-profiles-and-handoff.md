---
type: Architecture
title: Production Profiles and Handoff
description: Which production journey can actually admit writer safety, why the local one cannot, and the exact boundary of the Forgejo git handoff.
tags: [terminal-operator, admission, git-handoff]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [admission-profiles, contained-carrier, forgejo-handoff]
libraries: [bun]
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Production Profiles and Handoff

## Three journeys, one of which can admit

| Module / profile | Emits | Can admit writer safety? |
|---|---|---|
| `production-journey.ts` | legacy receipt | **No — legacy only, must not be used for admission** |
| `writer-production-journey.ts` (local) | `small-loop-run-receipt` **v1** | **No** — the gate expects v2 |
| `os-contained-production-profile.ts` | preflight receipt | **No** — preflight PASS cannot admit writer safety |
| `writer-contained-production-profile.ts` (upstream, via `production-use.profile.json`) | v2 receipt | **Yes** — it invokes the real repo-local `writer-entrypoint.ts` inside the OS-contained carrier |

`SKILL.md` states the rule directly: *"Direct host execution of either journey is invalid for
admission."* Admission requires the digest-pinned local OS carrier with network disabled and bounded
PID, CPU and memory resources — not a process started on the host.

The v1/v2 gap is the practical trap: the locally present journey emits v1, and the profile the gate
consults expects v2 from a script that lives in the absent `skills/repo-neural-perception/` tree. So
**no admission-valid production evidence can be produced from this checkout**. See
[Terminal operator overview](overview.md).

## Production-use rules the profile encodes

From `SKILL.md`, these are the properties the production profile exists to check:

- No shared-state read/await/write sequence. Use process isolation, or one explicit bounded lock/lease
  owner with a fixed acquisition order.
- No empty catch and no default-value fallback after a system failure; preserve the original diagnostic
  and emit a typed nonzero receipt.
- Own and close every subprocess, stream, timer, listener and network handle. Drain streams before
  awaiting exit; on timeout kill, await termination, and clear the timer in deterministic cleanup.
- Do not promote process groups, inherited descriptors or preload PID registries into
  arbitrary-descendant ownership.
- Give external calls hard deadlines and cancellation signals.
- `Promise.allSettled` only for explicit partial success, or to await every owned task for complete
  cleanup — without partial-success semantics, any rejection still fails the batch.
- Do not add a mutex unless shared mutable state crosses an async or process boundary.

`bounded-subprocess.ts` (`runBoundedProcess()`, `BoundedProcessOptions`, `BoundedProcessResult`) is the
shared primitive for the subprocess half of those rules.

## Sealed review is not async admission

Production-use is synchronous by default. Expensive review may move behind an immutable seal only
through the lifecycle in the upstream `async-production-seed.md`, and the current production command
must never be detached against a mutable checkout.

A sealed-review coordinator receipt is only one internal stage: `degraded`, missing-final,
mutable-checkout, unfenced and non-atomic receipts are rejected, and a reviewer PASS is **not** async
admission. The admission verb lives on the facade — see [Async lifecycle](async-lifecycle.md).

## Forgejo git handoff

`forgejo-git-handoff-cli.ts` accepts an **admitted** request and nothing else. Two request types:

### `forgejo-git-handoff-request@v1`

May add, or idempotently reuse, only the `forgejo` remote. Pushes the exact hash-bound HEAD to
`refs/heads/main` **without force and without upstream mutation**, preserves `origin`, and reads the
remote hash back. Every Git process is bounded and fully drained.

Typed nonzero failure on: stale HEAD, conflicting remote, bootstrap hash drift, cleanup failure, or a
readback mismatch. `forgejoPushArgs()` builds the argv; `GitHandoffFailure` is the typed error.

### `forgejo-pr-branch-handoff-request@v1`

Bounded to a `refs/heads/pr/*` target and **create-only**: it validates the ref, proves the remote ref
is absent before pushing, forbids force and upstream mutation, and reads the exact hash back. An
existing target ref **fails closed** rather than being updated.

### The boundary

This seam publishes Git state. It does **not** create repositories, issues, pull requests, or merges,
and does not touch cloud state. Publishing a review branch is not opening a review.

## Evidence boundary

Read from vendored source. No Bun toolchain, no local tests, and the admission-valid profile's script is
absent from this checkout.
