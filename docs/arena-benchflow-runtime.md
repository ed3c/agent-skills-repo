# First real Arena baseline/candidate runtime

This document records the first attempted real execution profile for issue
#15. It remains the immutable description of that attempt, not a public
leaderboard claim and not a currently executable provider profile.

## Pinned profile

| Dimension | Value |
|---|---|
| SkillsBench source | `benchflow-ai/skillsbench@9a1f4dd5f7659f75707435da3ce854b6e48321d1` |
| Task | `dialogue-parser` |
| Arena bundle | `sha256:822261046590e0521d715920df5612c55c2d76b1cc3bb3f43aa45123d2ed2b57` |
| Candidate task-local skill | `dialogue-graph` |
| BenchFlow | `0.6.3` |
| Agent | `pi-acp` |
| Model provider | GitHub Models |
| Catalog model | `openai/gpt-4.1-mini` |
| Effective model ID | `github-models/openai/gpt-4.1-mini` |
| Sandbox | Docker |
| Repetitions | 3 baseline + 3 candidate |
| Usage telemetry | required |
| Internal retries | 0 |
| Concurrency per invocation | 1 |

As originally designed, GitHub's Models catalog endpoint was queried with the
workflow-scoped `GITHUB_TOKEN`, `models: read`, and API version `2026-03-10`.
The repository policy remains unchanged so the failed attempt keeps its exact
meaning.

## Provider retirement boundary

GitHub's source statement says that GitHub Models, including its catalog and
inference APIs, was retired on 2026-07-30. The statement gives a date but not a
time of day. The repository therefore keeps that observation as the date-only
`retired_on` fact and separately adopts `2026-07-31T00:00:00Z` as its
fail-closed enforcement policy. This preserves every possible 2026-07-30
historical fetch; fetches at or after the policy timestamp are refused.

The official retirement statement is materialized as
`data/arena/github-models-retirement.json` and constrained by
`contracts/github-models-retirement-authority.schema.json`. The record keeps two
explicit states. `source_statement` binds the date-only claim, official URL,
observer's stable GitHub account identity, observation timestamp, statement
digest, and its own revocation mechanism. `enforcement_policy` binds the exact
provider, catalog endpoint, model prefix, policy timestamp, stable decision
authority/time, source-statement digest, rationale, policy digest, and its own
revocation mechanism. Revocation requires a superseding reviewed record at the
same canonical repository path; #46 is only its coordination pointer. The outer
record has its own canonical digest. These are source and fail-closed
execution-policy authorities reviewed through repository delivery; neither is
sandbox admission, lifecycle, routing, or ranking authority.

The runtime CLI requires this authority explicitly. At or after the effective
timestamp it refuses the profile before token, filesystem, catalog, or model
prerequisites. The diagnostic names the fixed provider, policy timestamp,
digest-pinned official authority URL, and record digest. The catalog endpoint,
response body, exception reason, and credential values are never copied into
diagnostics.

Network fetch uses an internal UTC clock and rechecks the retirement policy
immediately before opening the endpoint. A caller cannot supply an historical
evidence timestamp to reopen network access, and the CLI does not reuse its
earlier prerequisite-check time. Historical evidence replay uses the separate
offline evidence validator and never reopens the provider endpoint. Tests may
inject a deterministic clock solely to exercise the pre-cutoff contract.

HTTP authorization, other HTTP status, transport, and catalog-schema failures
use separate sanitized diagnostics. Response bodies, exception reasons, and
credential values are never included.

This retirement guard does not select a replacement provider and does not
complete #15 or #46. A new provider requires a new versioned policy, explicit
credential and budget authority, a fresh six-invocation physical run, offline
replay, non-secret evidence landing, and repository-local delivery authority.

The workflow consequently requests no `models: read` permission and cannot
schedule the retired provider. Its contract job exercises the fail-closed guard.
The former `paired-runtime` body remains visibly disabled as an archival
outline of PR #42; the full commit above is the exact historical identity. A
replacement provider must add a new physical job under its own reviewed policy
rather than silently reviving it.

### PR #42 failed-attempt evidence gap

GitHub run `31307442167`, job `93229900533`, at full commit
`3ecfe059260c96ad1df4d2360775904a32949df1` has a readable external job log
showing `ATTEMPT_OUTCOME=failure`, offline verification skipped, and the
observed catalog `HTTPError` before invocation 1. Artifact metadata identifies
artifact `9036379174`, named `arena-benchflow-dialogue-parser-1`, with upload
digest `8dc68ad8623a8bb26387e209aee956a11162deac12fb8a541f6130eba9ca60c7`.

That metadata is not a repository-local evidence bundle. During issue #47
review, two independent archive-download paths both returned HTTP 403, so the
raw `workflow-attempt.json`, stdout, and stderr cannot be read back or hashed
here. No full invocation identity, repository-local cleanup proof, or landed
artifact manifest exists. This is an explicit evidence gap: the observed
physical failure must not be dropped from the denominator, but it contributes
no qualification evidence and cannot satisfy #15. PR history, CI state, and
the metadata above are not substitutes for the missing raw receipt.

## Why the Arena does not call `bench skills eval`

BenchFlow's higher-level skill evaluation command owns its own arm scheduling
and retry behavior. The Arena already has a signed, preregistered run matrix and
must retain every planned invocation in its denominator. Delegating the matrix
to a second scheduler would create two authorities.

Each Arena invocation therefore runs exactly one native command:

```text
bench eval run --config <one-invocation-config>
```

The generated config fixes:

```yaml
concurrency: 1
max_retries: 0
usage_tracking: required
```

The baseline config uses `skill_mode: no-skill`. The candidate config uses
`skill_mode: with-skill` and the pinned task-local skill. Pairing, repetition,
and arm order remain owned by the signed Arena plan.

## Preparation boundary

Before signing the plan, the runtime:

1. validates the imported SkillsBench bundle with the existing structural
   authority;
2. verifies that the task exposes exactly one pinned skill;
3. computes the candidate skill artifact digest from every file, mode, size,
   and SHA-256 under its directory;
4. builds the task Docker image and records its immutable image ID and Docker
   server version;
5. validates provider retirement authority, then queries and digests the exact
   GitHub Models catalog entry only when the profile was active;
6. combines runtime-policy and model-catalog digests into one effective policy
   digest.

The Docker image is rebuilt before every invocation. If its immutable ID no
longer equals the preregistered ID, the invocation is refused before the model
runs.

## Evidence and safety rules

Each invocation preserves:

- generated BenchFlow YAML;
- scrubbed process stdout and stderr;
- scrubbed `result.json`;
- structured ACP trajectory JSONL;
- verifier output;
- task output artifacts;
- token, cost, latency, CPU, memory, and tool-call metrics;
- task, image, model-catalog, and policy bindings.

The adapter accepts only the tool names in the versioned runtime policy. An
unexpected tool becomes an explicit infrastructure-policy failure. Token-like
values and secret-key fields are scrubbed before materialization, and the final
checker scans every regular file for token-shaped strings.

The following remain distinct outcomes:

```text
succeeded
task_failure
verifier_failure
agent_refusal
timeout
transport_loss
infrastructure_failure
```

No outcome is silently retried. A process-level timeout and an absent
`result.json` still produce typed invocation evidence.

## Offline verification

The workflow publishes only public Ed25519 keys. The final checker:

- verifies all JSON schemas;
- verifies runtime-policy, catalog, preparation, image, and tool bindings;
- replays the signed experiment bundle without model credentials;
- independently rematerializes the paired result;
- verifies artifact file-set, size, and SHA-256 commitments;
- scans for leaked token shapes;
- requires all three baseline and all three candidate invocations to be scored.

## Interpretation boundary

A successful run proves that one pinned task can be executed through the full
Arena evidence path and that the observed six-invocation result is replayable.
It does not estimate general skill lift. The paired document is forced to keep:

```text
ranking_claim_allowed: false
```

Issue #15 closes only after a live replacement-provider implementation and its
non-secret raw evidence land on `main` through the repository landing
authority. The retired profile and its failed attempt do not satisfy that
boundary.
