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
