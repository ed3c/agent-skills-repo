# Quote-repair diagnostic preregistration

Issue #53 asks a narrower question than qualification: does the landed
`nearest_source_span` diagnostic improve one-attempt repair of planted interior
elisions? This document describes the signed, execution-disabled plan. It does
not report an effect.

## Immutable comparison

The baseline is the last landed portable `repo_wiki_verified` artifact before
PR #52, at commit `7bc9d28c8b2b4b187713654c84c792f2283e3c8e` and portable digest
`sha256:8019007c5eb01a1a7c15adc6a2c6afbeae12ba0df3e16d0c370b58120dbb2abb`.
The candidate is PR #52's merge commit
`5e73c53c0515a3ae982a9c62933903d359c5e9ff` and portable digest
`sha256:a6a9a1ea9c5337c2d2eb6131e00604c9a08879fe1bae66fa787fbbf2f0fda5da`.
The study protocol also binds each source artifact digest; the offline checker
reads both manifests back from those exact commits.

The v1 Arena plan hard-coded `baseline` to `no-skill`, which is not the
comparison requested by #53. The additive v2 contract therefore introduces
`baseline_skill_artifact_digest` and `study_protocol_digest`, plus a new v2
signature domain. Existing v1 plans and replay remain byte-compatible.

## Eligibility and denominator

`data/arena/quote-repair-interior-elision-tasks.json` freezes three public,
synthetic tasks. The validator mechanically proves for every task that:

- the planted quote is absent from the exact source bytes;
- `nearest_source_span` returns `interior_elision`;
- the expected repair is the exact contiguous span reported by the oracle;
- the wiki template actually contains the planted quote.

Five paired repetitions per task and two randomized arms produce 30 signed
invocations. Every invocation counts. There is one repair attempt and zero
retry-until-pass behavior. Provider, transport, infrastructure, verifier, task,
malformed-output, wrong-span, no-candidate, and search-incomplete results remain
distinct. `no_candidate` and `search_incomplete` require a task-bound typed
adapter diagnostic; the frozen verifier rejects an untyped or malformed
diagnostic as `malformed_repair`.

## Frozen analysis

The primary endpoint is binary lexical repair within one attempt. The effect
estimator is paired risk difference in parts per million. Success requires both
a point estimate of at least 200,000 ppm and a 95% cluster-bootstrap lower bound
above zero. The bootstrap count (10,000), seed (530046), secondary endpoints,
and failure classes are frozen in
`data/arena/quote-repair-study-protocol.json`.

This is a diagnostic-efficacy study, not a ranking or qualification authority.
Both claims remain disabled.

## Signed state and execution boundary

The plan at
`data/verification_runs/quote_repair_preregistered_plan_2026-08-12.json` is
signed by an Ed25519 key generated outside the repository. Only the public key
and its plan binding are committed. The private key is not repository material.
Current trust is determined from
`data/arena/quote-repair-plan-trust.json`, whose repository-owner actor and
authority can revoke or supersede the key without rewriting historical plan
bytes. The public-key document retains the mint-time trust-registry digest for
provenance, while offline verification always reads the canonical current
registry.

The plan binds the proposed local provider policy from PR #55 and the observed
Docker image `sha256:808f6a9296f8d66e3017329b54aceb85a178ccc64d530d3e22c40ae5c3951571`.
The environment receipt binds the Dockerfile, verifier bytes, base image,
platform, and Docker server version. Its observer authority and current
revocation/supersession state are read from the canonical environment trust
registry. It records no execution cleanup proof, because no efficacy workspace
has run.

Most importantly, both the study protocol and public-key document retain
`execution_authorized: false`. Merging this preregistration freezes the design;
it does not start the 30 invocations. A separate reviewed admission must bind
the exact landed plan hash, provider policy, zero external API budget, and run
window before physical execution.
