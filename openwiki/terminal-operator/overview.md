---
type: Architecture
title: Terminal Operator Overview
description: The vendored Bun/TypeScript terminal-slice operator — packet contract and v1/v2 preflight, the phased task-quality gate and its test-count rule, the typed-receipt rejection rules, and the external dependencies that make this copy unrunnable.
tags: [terminal-operator, vendored, contracts]
node_kind: RepoDoc
ingest_lane: concept
repo: local/agent-skills-repo
covers: [terminal-slice-packet, preflight-checks, phased-quality-gate, vendored-boundary]
libraries: [bun]
generated_by: claude-code+opus (skill-bettor openwiki port)
generated_at: null
---

# Terminal Operator Overview

`.agents/skills/repo-terminal-operator/` is ~9.7k lines of Bun TypeScript implementing a **leased
terminal-slice implementer**: an agent handed one bounded slice of work, which verifies it may touch it,
does it, and returns a typed receipt. It is invoked when `repo-neural-perception` delegates a
`terminal-slice-packet@v2` to this repository.

## Three tiers of what is actually here

Read this first; it bounds what any other statement on these pages can mean.

**Vendored and present** — 50 `.ts` modules, `code-quality.profile.json`,
`production-use.profile.json`, `evidence-cost-collector.production.profile.json`,
`generated-core/manifest.json`, and `SKILL.md`.

**Required but absent** — `runtime/` and `skills/repo-neural-perception/` (which own the
`production-use.md` SSOT, `writer-contained-production-profile.ts`, and the packet validator), adapter
configuration, schemas, and the Bun test suites `task-quality-contract.ts` references. There is **no
`package.json`, no `tsconfig.json`, and no local TypeScript test** in this tree.

**Concrete proof, not inference.** `repo-preflight.ts::inputContract()` does:

```ts
spawnSync("bun", ["run", resolve(workspaceRoot, "runtime/contracts/validate-packet.ts"),
                  "--schema", schema, "--input", inputPath], { cwd: workspaceRoot })
```

`runtime/contracts/validate-packet.ts` does not exist in this checkout, so preflight fails at its very
first check. This copy cannot be executed or verified standalone, and external test suites must not be
cited as evidence available here.

## Contracts

| Direction | Contract |
|---|---|
| input | `terminal-slice-packet@v2`, with `v1` retained for backward-compatible preflight |
| output | `small-loop-run-receipt@v1` |
| description | `repo-terminal-operator-description@v1` |
| preflight | `repo-terminal-preflight-receipt@v1` |
| code-quality receipt | `repo-terminal-code-quality-receipt@v1` |
| production receipt | `repo-terminal-writer-production-journey-receipt@**v2**` |

## v1 versus v2, precisely

`inputContract()` selects the schema by exact equality: `schema_version === "terminal-slice-packet@v2"`
picks v2, and **anything else — including a malformed or absent value — falls back to v1**. The
fallback is permissive on purpose (v1 packets must still preflight) but it means a typo in the version
string silently downgrades validation rather than failing.

The packet type carries `guided_claim_ids` always, and these **optionally**:

| Field | Role |
|---|---|
| `claim_set: { ref, sha256 }` | v2 only in practice — `claimSetCheck()` verifies the hash binding |
| `agentic_execution.tdd` | `red_receipt`, `green_receipt`, `tests_immutable_during_green` — verified by `tddReceiptCheck()` |
| `agentic_execution.minimal_diff` | `allowed_paths_only`, `unrelated_refactors` |
| `entrypoint` | the argv the slice is allowed to run |
| `target_repo`, `allowed_paths`, `write_lease{expires_at, expected_head}` | required |

`runPreflightChecks()` composes the result: always the input-contract check, plus a parse check when
reading the packet raised, plus `packetChecks(...)` — target repo, entrypoint, lease, HEAD, allowed
paths, generated-core bundle, claim set and TDD receipts. A preflight passes only when **every** check
is `passed`.

### `entrypoint-reachable`, exactly

`entrypointCheck()` accepts one argv shape and nothing else. The rule is **identical for v1 and v2
packets** — the schema difference lives in `claim_set` and `agentic_execution`, not here.

| Step | Rule | Failure detail |
|---|---|---|
| shape | `entrypoint[0] === "bun"`, `entrypoint[1] === "run"`, and `entrypoint[2]` present and non-empty | `entrypoint must begin with bun run <script>` |
| containment | `realpath(resolve(target, entrypoint[2]))` must be contained in `realpath(workspaceRoot)` | `entrypoint escapes workspace` |
| kind | that real path must be a regular file | `entrypoint is not a regular file` |
| any throw | `realpathSync`/`statSync` errors — typically a non-existent script | the raw error message |

Two consequences. **Only `bun run` is reachable** — no `node`, no bare script, no extra leading flags;
an argv of `["bun", "run", "--watch", "x.ts"]` fails, because index 2 must already be the script.
And containment is checked **after** `realpath`, so a symlink pointing outside the workspace is
rejected rather than followed.

On success the detail is `relative(target, realScript)`, so the receipt records which script the
entrypoint actually resolved to rather than what the packet claimed.

`allowedPathsCheck()` uses the same containment helper against `outputRepo` and fails with the escaping
paths joined by commas — an absolute path in `allowed_paths` is always an escape.

## CLI surface

`repo-adapter.ts` resolves `outputRepo` two levels above the module directory and `workspaceRoot` two
above that:

```sh
bun run repo-adapter.ts --describe            # profile summary
bun run repo-adapter.ts --selftest            # same, but fails if either profile has no real command
bun run repo-adapter.ts --preflight <packet>  # lease/HEAD/path gate only
bun run repo-adapter.ts --run <packet>        # preflight -> code-quality -> production-use -> receipt
```

Exit codes: `0` passed, `2` failed, `64` usage.

## The phased task-quality gate

`task-quality-contract.ts` declares the stages; `task-quality-runner.ts::executeTaskQuality()` runs
them. The orchestration rule is short and strict:

1. Phases are the sorted distinct `phase` values across all stage definitions.
2. **Before each phase**, if *any* receipt so far is not `passed`, every stage of this phase is pushed
   as `blocked` — it never runs.
3. Otherwise the whole phase runs concurrently via `runPhase()`, which uses `Promise.allSettled` and
   therefore **collects every stage's result**, including the failures. A phase does not abandon its
   siblings when one stage fails.
4. A rejected stage runner (not merely a failing command) becomes a receipt with `status: "failed"`,
   the rejection message in `cleanup_errors`, and `diagnostic: "stage runner rejected"` — distinct
   from a command that ran and failed.

So `blocked` means "an earlier phase failed", never "this stage failed"; its receipt carries
`exit_code: null`, all cleanup flags false, and `diagnostic: "blocked by an earlier phase"`.

The aggregate receipt is `passed` only when **every** receipt — including blocked ones — is `passed`,
and the resource flags (`process_reaped`, `timer_cleared`, `stdout_consumed`, `stderr_consumed`) are
computed over non-blocked stages only, so a blocked stage cannot dilute them.

`coverage` is fixed at `{ status: "not-selected", next_mode: "production-use/writer-entrypoint" }` —
the task profile deliberately declines to claim an aggregate coverage number over subprocess-tested
modules and routes coverage, race and leak admission to the production profile instead.

## A clean exit is not enough for a test stage

`clean(result)` requires **all seven** of: `exitCode === 0`, `!timedOut`, `processReaped`,
`timerCleared`, `stdoutConsumed`, `stderrConsumed`, and `cleanupErrors.length === 0`.

For a test stage — identified structurally as `command[0] === "bun" && command[1] === "test"` — that is
still not sufficient. `testCases()` scans combined stdout+stderr for `/(?:^|\n)\s*(\d+) pass\s*(?:\n|$)/`
and the stage passes only when a count was actually observed:

```ts
const passed = clean(result) && (!testStage || observedTestCases !== null);
```

This is `SKILL.md`'s *"exit code zero without an observed test count is a failure"* made mechanical. A
suite whose filter matched nothing exits 0 and prints no `N pass` line — and is therefore recorded as
`failed`, not as a vacuous success. The observed count is persisted in the receipt as `test_cases`.

## `gateReceiptErrors` — what a gate receipt must satisfy

`small-loop-gate-contract.ts::gateReceiptErrors(name, result, workspaceRoot)` first requires the
receipt to parse, to carry the exact expected `schema_version` for its name, and to be `status:
"passed"` — otherwise the single error `typed receipt must pass <schema>`. Then it branches:

**`code-quality`** (`codeQualityErrors`): the inner receipt must have `exit_code: 0`,
`timed_out: false`, `cancelled: false`, `process_reaped: true`, `timer_cleared: true`,
`stdout_consumed: true`, `stderr_consumed: true`, an empty `cleanup_errors` array, and a `command`
**deeply equal** to `REPO_TERMINAL_CODE_QUALITY_COMMAND`. That last check is the profile-drift guard:
a receipt produced by a different command cannot be presented as this gate's evidence. Per-field
failures are reported as `inner <field>` so the diagnostic names the exact mismatch, and
`taskQualityReceiptErrors()` adds the stage-level checks.

**`production-use`** (`productionErrors`): `verifiedProductionSafety(value, "writer-entrypoint")` must
sort to exactly `["race-condition", "resource-leak", "silent-failure"]` — all three or
`production safety evidence incomplete`. It then builds a `ProfileSummary` with `declared: 1`,
`executed: 1`, the artifact path and SHA-256, the observed `evidence_scope`, and
`requiredEvidenceScope: "writer-entrypoint"`, and hands it to `validateProductionArtifact()`. A receipt
whose evidence scope is anything other than `writer-entrypoint` fails here — which is exactly how a
preflight-only or legacy journey is prevented from admitting writer safety.

## `runSmallLoop` — HEAD is checked twice

```text
parsePacket -> head() [initial] -> preflight -> executeGates -> head() [actual] -> buildReceipt
```

- `initialHead !== expected_head` → a `git-head` / `stale` failure.
- `actualHead !== expected_head` → a **second** `git-head` / `stale` failure, recorded independently.

Reading HEAD before and after is what catches a repository that moved *during* the run: the lease was
valid when the work started and is not valid now, and the receipt says so rather than publishing a
candidate against a tree that changed underneath it.

`executeGates(preflightPassed, …)` is passed the preflight verdict, so code-quality and production-use
do not run at all when preflight failed. Failures accumulate from three sources — preflight failures,
gate failures, and the two HEAD comparisons — and `buildReceipt()` also records
`changedPaths(packet.allowed_paths)`, so the receipt states which leased paths actually changed rather
than asserting the lease was respected.

The resulting `small-loop-run-receipt@v1` therefore distinguishes three outcomes: `passed`, `failed`
(something ran and failed), and stages recorded as `blocked` (an earlier phase failed, so they never
ran).

## What it may never do

From `SKILL.md`, worth stating because several modules look like they could:

- Return `small-loop-run-receipt@v1` — but **not** admit, merge, release, or rewrite Git history.
- A preflight PASS emits the next writer-production mode; it **cannot** admit writer safety.
- The control plane may project queue and redrive refs but must not discover implicit work, execute
  workers, update `計畫.md`, change Forgejo, or admit a result.
- The evidence-cost projector never runs a worker or grants admission.
- The Forgejo seam pushes Git state but cannot create repositories, issues, PRs, or merges.

## Pages

- [Async lifecycle](async-lifecycle.md) — seal, CAS, lease, fencing, disposition matrix.
- [Writer publication](writer-publication.md) — the atomic persistence core nine modules depend on.
- [Evidence cost](evidence-cost.md) — cost measurement and the unclosed v1/v2 handoff.
- [Production profiles and handoff](production-profiles-and-handoff.md) — which profile can admit, and
  the Forgejo boundary.
