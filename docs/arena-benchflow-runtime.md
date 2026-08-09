# First real Arena baseline/candidate runtime

This document defines the first real execution profile for issue #15. It is a
single-task runtime proof that the reusable experiment contract can drive a
real agent, not a public leaderboard claim.

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

GitHub's Models catalog endpoint is queried at runtime with the workflow-scoped
`GITHUB_TOKEN`, `models: read`, and API version `2026-03-10`. The safe catalog
fields and their digest are published; the token is not. The repository policy
pins the model ID, while the catalog evidence records the version actually
advertised during the run.

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
5. queries and digests the exact GitHub Models catalog entry;
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

Issue #15 closes only after this runtime implementation and its non-secret raw
evidence land on `main` through the repository landing authority.
