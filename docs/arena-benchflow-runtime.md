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

GitHub retired GitHub Models, including its catalog and inference APIs, on
2026-07-30. The first physical workflow attempt on 2026-08-09 therefore failed
before invocation 1. That failure remains a physical failed attempt; it is not
deleted, retried under another identity, or reinterpreted as a model outcome.

The official retirement statement is materialized as
`data/arena/github-models-retirement.json` and constrained by
`contracts/github-models-retirement-authority.schema.json`. The runtime CLI
requires this authority explicitly. On or after the recorded retirement date,
it refuses the profile before opening the catalog URL and emits only the fixed
provider ID, retirement date, and reviewed authority URL. Pre-retirement
catalog evidence keeps its historical replay semantics.

HTTP authorization, other HTTP status, transport, and catalog-schema failures
use separate sanitized diagnostics. Response bodies, exception reasons, and
credential values are never included.

This retirement guard does not select a replacement provider and does not
complete #15 or #46. A new provider requires a new versioned policy, explicit
credential and budget authority, a fresh six-invocation physical run, offline
replay, non-secret evidence landing, and repository-local delivery authority.

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
