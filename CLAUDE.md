# Claude Code repository contract

## Mandatory entrypoint

Before changing Skill sources, portable exports, sandbox execution, signing, verification, admission, Arena evaluation, or Atlas integration, read [`INTEGRATION_REQUIREMENTS.md`](INTEGRATION_REQUIREMENTS.md) in full. Then read the exact contracts, data, scripts, evidence records, tests, and generated artifacts affected by the task.

If a handoff, digest, receipt, attestation, manifest, trust root, public key, cleanup proof, landing authority, test, or committed path cannot be read back from its declared authority, report an evidence gap. Do not infer delivery or qualification from README text, issue comments, PR bodies, Projects state, expected paths, short commit IDs, or prior conversations.

## Required behavior

1. Keep portability, source anchoring, local correctness, sandbox qualification, verification, admission, production routing, implicit invocation, and Arena ranking as separate states.
2. Execute the exact immutable Atlas handoff; never rebuild a moving or prose-described substitute.
3. Fail closed on digest, scope, host, profile, policy, task-pool, threshold, freshness, replay, trust, license, security, cleanup, or reproduction mismatch.
4. Never commit, log, print, fixture, or upload signing private keys.
5. Preserve physical execution failures in the denominator and evidence bundle.
6. Treat contract CI as non-qualification evidence unless a real declared sandbox execution and cleanup proof exist.
7. Require independent signature verification before admission and separate reviewed authority before lifecycle/routing changes.
8. Modify canonical sources first; generated `dist/` artifacts must be deterministic projections.
9. Bind delivery to a full commit, exact changed paths, digests, tests/receipts, and repository-local landing evidence.
10. Report `implemented`, `tested locally`, `physically executed`, `signed`, `verified`, `admitted`, `merged`, and `production-routable` separately.
