# Local Arena provider policy and capability boundary

Issue #46 now has one proposed, credential-free provider envelope under
`data/arena/ollama-qwen3-4b-local.json`. It pins Ollama `0.15.5`, the
`qwen3:4b` tag and content digest, BenchFlow `0.6.3`, `pi-acp`, Docker, the
OpenAI Chat Completions protocol, and separate host/sandbox endpoint identities.
The host URL is loopback-only; the sandbox URL uses Docker's
`host.docker.internal` bridge. Neither URL is a public provider endpoint.

The policy records `credential_kind: none` and an external API cost ceiling of
zero. It permits exactly one capability-probe model invocation. That allowance
does not authorize an efficacy experiment: the canonical policy remains
`selection_status: proposed` and `experiment_execution_authorized: false`.
A later reviewed policy revision must bind a signed preregistration before any
#53 invocation.

Observation authority is explicit rather than inferred from the coordination
issue: actor `github:ed3c` records local evidence under
`repository-owner-review@1`. The append-only revocation/supersession surface is
`data/arena/provider-observation-revocations.json`; every attempt binds its
current registry digest, and offline replay rejects listed attempt IDs.

## Capability preflight

`scripts/preflight_arena_provider.py` has two distinct modes:

- `--validate-only` checks the immutable policy without network or model access
  and reports `provider_ready: false`;
- physical mode requires separate exclusive attempt and capability-receipt
  paths. It registers the attempt before network access, records the transition
  into the single model invocation, and finalizes either a sanitized failure or
  a successful receipt digest. An existing attempt path fails closed, so the
  same bounded allowance cannot be silently retried.

The HTTP client accepts only `http://127.0.0.1:<port>/v1` and
`http://host.docker.internal:<port>/v1`, caps each response at 1 MiB, never
sends a credential, and reduces HTTP or transport failures to sanitized error
categories. It does not persist model prose or reasoning. The tool-call probe
sets `stream: false` and `think: false` and accepts only one
`provider_probe({"value":"PROVIDER_OK"})` call.

The physical capability receipt at
`data/verification_runs/ollama_qwen3_provider_preflight_2026-08-12.json`
and attempt receipt at
`data/verification_runs/ollama_qwen3_provider_attempt_2026-08-12.json` bind one
successful observation to the local observer host digest, runtime identity,
attempt ID, and one-invocation denominator. The capability receipt's
`ready: true` is historical observation, not a promise that a mutable service
remains available. A fresh, separately registered preflight is mandatory
immediately before a separately authorized experiment.

## Offline verification and claim boundary

`scripts/check_arena_provider_preflight.py` verifies the policy, capability,
and attempt bindings; both digests; provider/model identity; capability
booleans; token arithmetic; one-invocation denominator; zero credential use;
zero external API cost; and the still-false execution authority without
contacting Ollama. Its output says `receipt_verified` and `observed_ready` with
the observation timestamp—never `provider_ready`. CI performs this historical
offline replay; it does not repeat or qualify the physical model call.
Terminal failed attempts are verified without a capability receipt and remain
in the denominator; non-terminal `started` or `invoking` records cannot pass the
offline checker.

This slice establishes a proposed provider policy and physical capability
observation only. It does not complete #46, because the original six-invocation
matrix, signed evidence, cleanup proof, independent experiment replay, and
repository-local completion authority are absent. It also does not complete
#53: no signed quote-repair preregistration or efficacy invocation exists, and
no qualification, admission, Arena ranking, or production-routing claim is
made.
