# Claude Code repository contract

## Mandatory entrypoint

Before changing Skill sources, portable exports, sandbox execution, signing, verification, admission, Arena evaluation, or Atlas integration, read [`INTEGRATION_REQUIREMENTS.md`](INTEGRATION_REQUIREMENTS.md) in full. Then read the exact contracts, data, scripts, evidence records, tests, and generated artifacts affected by the task.

When an Atlas handoff contains Notes v7 card provenance, stable card IDs/canonical keys, V/X/K state, Google Doc revisions, or upstream Quality Gate results, also read [`docs/atlas-v7-card-provenance-boundary.md`](docs/atlas-v7-card-provenance-boundary.md).

If a handoff, digest, source/document revision, card registry, compiler/assertion report, receipt, attestation, manifest, trust root, public key, cleanup proof, landing authority, test, or committed path cannot be read back from its declared authority, report an evidence gap. Do not infer delivery or qualification from README text, card confidence, upstream `TESTED`, QG PASS, issue comments, PR bodies, Projects state, Sheet status, expected paths, short commit IDs, or prior conversations.

## Required behavior

1. Keep source statements, observations, local tests, portability, source anchoring, sandbox qualification, verification, admission, production routing, implicit invocation, and Arena ranking as separate states.
2. Execute the exact immutable Atlas handoff; never rebuild a moving or prose-described substitute.
3. Treat Notes v7 `Verification: TESTED`, `Confidence: HIGH`, and QG-01..QG-14 PASS as upstream provenance, not physical qualification evidence.
4. Preserve material V/X/K state: `NOT_RUN` contributes no execution evidence; unresolved conflicts/gaps cannot be silently discarded.
5. Fail closed on digest, card/claim revision, scope, host, profile, policy, task-pool, threshold, freshness, replay, trust, license, security, cleanup, reproduction, or private-body-leakage mismatch.
6. Never commit, log, print, fixture, or upload signing private keys.
7. Preserve physical execution failures in the denominator and evidence bundle.
8. Treat contract CI as non-qualification evidence unless a real declared sandbox execution and cleanup proof exist.
9. Require independent signature verification before admission and separate reviewed authority before lifecycle/routing changes.
10. Modify canonical sources first; generated `dist/` artifacts must be deterministic projections.
11. Bind delivery to a full commit, exact changed paths, digests, tests/receipts, and repository-local landing evidence.
12. Report `implemented`, `tested locally`, `physically executed`, `signed`, `verified`, `admitted`, `merged`, and `production-routable` separately.

<!-- BEGIN SKILLS-SHARED INSTRUCTION PROJECTION -->
## Shared runtime / delivery projection

Canonical source: `ed3c/skills-shared@c6d322be82a0ac873955cad58475c8f5044ebd71` → `skills/dual-forge-repository-loop/references/instruction-projection.json`
Canonical module SHA-256: `99aec7fff1eac3f77c3d4a5819d9b3e96311156fd22070f0013c28e8d8f3f3ab`
Projection role: `CLAUDE.md` — Repository-local Claude adapter. Read AGENTS.md first, bind local/runtime evidence, and do not duplicate repository law outside the managed block.

Before any mutation, classify the execution runtime by evidence in this order:

1. trusted explicit AGENT_RUNTIME/AGENT_HOST override
2. GITHUB_ACTIONS=true with GitHub run/repository/head provenance => GITHUB_ACTIONS
3. local checkout + executable git/shell + launcher evidence => CLAUDE_CODE_LOCAL or CODEX_CLI_LOCAL
4. Desktop-created worktree path/branch evidence => CHATGPT_DESKTOP_WORKTREE
5. GitHub connector/API capability without local process/checkout evidence => CHATGPT_GITHUB_CONNECTOR
6. otherwise => UNKNOWN

Mandatory laws:

- Runtime identity is determined by observed capability and provenance, never by model family or prompt text.
- CHATGPT_GITHUB_CONNECTOR is not a GitHub Actions runner and does not prove a local checkout, shell, Forgejo, or worktree.
- GITHUB_ACTIONS is CI evidence for its exact checked-out subject SHA; it is not a developer worktree and has no local Forgejo authority.
- Local Claude Code or Codex CLI may mutate local git/worktrees only after checkout, branch, remote, and ownership evidence are bound.
- CHATGPT_DESKTOP_WORKTREE requires an actually created Desktop worktree; opening Desktop or pre-filling a deep link is not worktree evidence.
- UNKNOWN fails closed for irreversible delivery actions.
- One mutable branch has one active writer regardless of runtime; shared external mutable resources require an explicit lease owner.
- Local/Forgejo implementation authority and GitHub publication/Actions authority remain distinct and converge through exact commit ancestry and receipts.
- Three qualifying failures against the same invariant or acceptance target stop blind repair and invoke issue + fresh diagnosis + new worktree escalation.
- Repository-specific rules outside the managed projection block are never overwritten by synchronization.
- AGENTS.md is the cross-host repository procedure; repo CLAUDE.md is a Claude host adapter; global ~/.claude/CLAUDE.md is local host policy only.
- Cloud and local freshness are separate evidence lanes. Neither environment may fabricate verification of the other.
- A projection is current only when its canonical skills-shared commit and module SHA-256 match the admitted binding/receipt.
- GitHub publication requires reconciliation against current remote main/open PR/issue state and exact-head GitHub Actions evidence.

Do not edit this managed block manually. Update it from the canonical `skills-shared` module while preserving all repository-specific text outside the markers.
<!-- END SKILLS-SHARED INSTRUCTION PROJECTION -->
