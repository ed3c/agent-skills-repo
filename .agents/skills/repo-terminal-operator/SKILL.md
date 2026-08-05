---
name: repo-terminal-operator
description: Apply one typed terminal slice inside this repository and own its code-quality and production-use verification. Use when repo-neural-perception delegates a terminal-slice-packet@v2 to agent-skills-repo.
---

# Repo Terminal Operator

<!-- production-safety-derived-profile@v1; SSOT: skills/repo-neural-perception/references/production-use.md -->

1. Prefer `terminal-slice-packet@v2`; retain v1 only for backward-compatible preflight. V2 must hash-bind its complete `guided-claim-set@v1`.
2. Verify objective, invariants, public contract, failure modes, bounded active files, red/green receipts, minimal-diff assertions, target repository, expected Git head, live write lease, allowed paths, and generated-core bundle before editing.
3. Run only argv-array commands; never interpolate a packet into a shell string.
4. Modify only the leased terminal slice. Stop on scope drift and return a structured failure.
5. Run `code-quality.profile.json` and `production-use.profile.json` independently.
   Derive the Bun/TypeScript static tool floor from
   `loop_wiki/evolve-unknown-discovery-plan-truth/modules/development-standards.md`;
   `production-use.md` adds physical failure semantics and does not replace that
   floor.
   The normal task-quality macro-step runs four bounded static stages in parallel
   (typed ESLint, Prettier check, strict TypeScript, dependency boundaries), then
   runs the bounded focused terminal behavior suites only when every static stage
   passes. Consume its typed stage receipts; exit code zero without an observed
   test count is a failure. This task profile deliberately records coverage as
   `not-selected` and routes coverage/race/leak admission to
   `production-use/writer-entrypoint` instead of claiming a misleading aggregate
   threshold over subprocess-tested modules.
6. Match the production profile's `required_evidence_scope` to the physical receipt. A preflight PASS must emit the next writer-production mode and cannot admit writer safety.
7. Return `small-loop-run-receipt@v1`. Do not admit, merge, release, or rewrite Git history.
8. Keep production-use synchronous by default. Expensive review may move behind an immutable seal only through the lifecycle in `skills/repo-neural-perception/references/async-production-seed.md`; never detach the current production command against a mutable checkout.
   A sealed-review coordinator receipt is only one internal stage. Reject `degraded`, missing-final, mutable-checkout, unfenced, or non-atomic receipts and do not treat reviewer PASS as async admission.
9. Use `async-job-lifecycle.ts` only as the internal durable state-machine prerequisite: it atomically seals candidate + foreground receipt + production job, appends monotonic CAS events, fences expired workers, and resolves cancel/finish races. It does not launch an Agent, mutate the live checkout, admit a result, publish Git state, or enable background production.
10. Use `async-control-plane-cli.ts --request <request.json>` only for an explicit `project-only` request. It may project one or many named runs, persist deterministic queue/redrive refs, classify but never delete orphan transactions, and emit the next prompt. It must not discover implicit work, execute workers, update `計畫.md`, change Forgejo, or admit a result. A separately activated local dispatcher owns the projected retry budget; until that dispatcher passes production admission, queue refs remain advisory.
11. Use `evidence-cost-cache-cli.ts --request <request.json>` only after referenced receipts exist. The request must hash-bind every source input, the cross-cutting plan, toolchain argv/version, stage evidence, oracle relation, and a separate cost-observation receipt; cost fields are derived from the reopened observation, never accepted directly from the request. Atomically publish and reopen a canonical content-addressed request before classifying cache hits. Treat `external-hash-bound-observation/unadmitted-collector` as projected evidence, name its aggregate axes `asserted_*`, and always route it to the trusted collector node rather than foreground admission. Reuse a cache entry only after reopening all bindings. Preserve CPU, I/O, and LLM axes as `not-selected` or `not-applicable` rather than measured zero, and consume the generated next-mode prompt. This projector never runs a worker or grants admission; its four-projector race probe belongs to opt-in production mode, not the foreground edit loop.
12. Invoke `evidence-cost-collector-cli.ts --request <collector-request.json>` only with `REPO_EVIDENCE_COLLECTOR_PRODUCTION=1`. Reopen the plan, source inputs, typed passing stage evidence, canonical request, and ready-stage progress; bind the committed collector bundle, resolved executable hash/version, HEAD, and exact argv before using the owned Bun process-group carrier. Every Git/runtime/stage subprocess has a hard deadline and cancellation path; receipt finalization uses a separate bounded phase so cancellation remains diagnosable. The v1 collector physically measures wall time and direct-child CPU only; unavailable CPU is explicit failure, Bun I/O operation counts are diagnostic and MUST NOT be relabelled as bytes, and LLM tokens remain `not-selected` until a trusted provider receipt exists. Preserve bounded stdout/stderr tails plus their hashes, and persist precondition/system failures separately. Publish content-addressed request, execution, axis-scoped observation, and completion receipts; any timeout, cancellation, nonzero exit, stream/registry/sentinel leak, nested hash drift, or HEAD drift fails closed. The stage worktree/source closure is explicitly `not-selected`: collector HEAD proves its own bundle, not every file the stage may read. Keep `admission_eligible=false` and retain the same collector prompt while I/O bytes or LLM tokens are missing. The heavy profile is suitable for a later sealed background worker, but the dispatcher remains disabled.
13. Use `async-admission-facade-cli.ts --request <request.json>` as the only public async lifecycle seam. `start` reopens a current Git HEAD, bounded candidate refs, a producer-shared complete `small-loop-run-receipt@v1` assertion, and the complete shared worker-job parser before atomically sealing; it never launches a worker. `inspect` is read-only, `cancel` appends one monotonic lifecycle event, and `admit` reopens HEAD, candidate bytes, foreground receipt, job, lifecycle result, cleanup, isolation, and reviewer bindings before publishing one immutable ownership receipt. Two admitters must converge, only the first owns publication, and `cwd-only-degraded` remains advisory rather than admissible. Requests are anchored repo-relative artifacts and every CLI failure is a typed nonzero error. The facade does not dispatch work, change Git, write `計畫.md`, call Forgejo/GitHub/cloud, or discover implicit runs.
14. Use `forgejo-git-handoff-cli.ts` only for an admitted `forgejo-git-handoff-request@v1`. It may add or idempotently reuse only the `forgejo` remote, push the exact hash-bound HEAD to `refs/heads/main` without force or upstream mutation, preserve `origin`, and read the remote hash back. Every Git process is bounded and fully drained. Any stale HEAD, conflicting remote, bootstrap hash drift, cleanup failure, or readback mismatch emits a typed nonzero failure. This narrow seam publishes Git state but does not create repositories, issues, PRs, merges, or cloud state.
15. The same CLI may accept `forgejo-pr-branch-handoff-request@v1` only for a bounded `refs/heads/pr/*` target. That path is create-only: it validates the ref, proves the remote ref is absent before push, forbids force and upstream mutation, and reads the exact hash back. An existing target ref fails closed instead of being updated. It publishes a review branch but still cannot create or merge a PR.

## Operate the leased slice

After preflight passes, act as the terminal implementer rather than returning the packet unchanged:

1. Load only the packet contract, declared target files/tests, and two or three nearby repository precedents.
2. Add or verify a failing public-behavior test for the named normal, boundary, failure, cancellation, and cleanup paths before editing implementation.
3. Modify only `allowed_paths` while the expected HEAD and lease remain valid. Recheck both before publishing any candidate.
4. Run the packet's smallest focused validator, then the independent code-quality and production-use commands. A green test cannot replace either receipt.
5. Reopen every terminal artifact, record its SHA-256 and source claims, and emit the typed small-loop receipt plus `HARNESS-CROSS-CUTTING-*` lineage when the change spans shared harnesses.
6. Stop with changed paths and a typed diagnostic when any assertion fails; never create a success-shaped receipt merely because an implementation file exists.

For production-use work:

- No shared-state read/await/write sequence. Use process isolation or one explicit bounded lock/lease owner with fixed acquisition order.
- No empty catch or default-value fallback after a system failure. Preserve the original diagnostic and emit a typed nonzero failure receipt.
- Own and close every subprocess, stream, timer, listener, and network handle. Drain streams before awaiting exit; on timeout kill, await termination, and clear the timer in deterministic cleanup.
- Do not promote process groups, inherited descriptors, or preload PID registries into arbitrary-descendant ownership. Production admission uses the digest-pinned local OS carrier with network disabled and bounded PID/CPU/memory resources.
- Give external calls hard deadlines and cancellation signals. Treat cleanup failures as terminal failures, not log-only warnings.
- Use `Promise.allSettled` only for explicit partial success or to await every owned task for complete cleanup; without partial-success semantics, any rejection still fails the batch. Do not add a mutex unless shared mutable state crosses an async/process boundary.
- Run the smallest public-behavior test first, then the separate code-quality and production-use profiles. Do not claim writer race safety from a read-only preflight receipt.

Run `bun run repo-adapter.ts --describe` to inspect this profile and `bun run repo-adapter.ts --preflight <packet.json>` for the fast lease/HEAD/path gate. Use `bun run repo-adapter.ts --run <packet.json>` for the complete repo-local loop: it runs preflight, code-quality, then production-use, blocks production-use after a code-quality failure, and emits the physical `small-loop-run-receipt@v1`. Active admission evidence inside that loop must run through `bun run ../../skills/repo-neural-perception/scripts/writer-contained-production-profile.ts`; it invokes the actual repo-local `writer-entrypoint.ts` inside the OS-contained carrier. The preflight-only `os-contained-production-profile.ts` remains available for diagnosis but cannot admit writer safety. Direct host execution of either journey is invalid for admission. The older `production-journey.ts` is legacy-only and MUST NOT be used for admission.
