# Atlas / Notes v7 Provenance Boundary｜Qualification Plane

## Purpose

This repository qualifies one immutable Skill artifact under one declared host, sandbox, policy, task pool, threshold, and environment envelope.

An Atlas handoff may contain provenance derived from `ed3c/ai-content-notes` v7.0 cards. That provenance supports review and traceability. It is not physical qualification evidence.

## Upstream provenance fields

An immutable Atlas handoff may bind digests or identifiers for:

```text
source manifest
Google Doc revision or historical Git blob
card registry
compiler state and Quality Gate assertion report
card stable IDs / canonical keys / revisions / lifecycle
Evidence IDs and exact locators
Claim Kind / Verification / Confidence Basis
typed links
V verification cards
X conflict cards
K knowledge-gap cards
admitted Atlas claim IDs
```

Complete private source text, transcript, note body, credentials, private session traces, and unpublished evidence must not enter public qualification results or Arena artifacts.

## Non-equivalence invariants

The following are never equivalent:

```text
Notes card Verification: TESTED
  != sandbox qualification execution

Notes card Confidence: HIGH
  != qualification confidence or admissibility

Notes QG-01..QG-14 PASS
  != Skill correctness under a host/profile/task pool

Atlas admitted Claim
  != successful Skill execution

local test artifact
  != independently sandbox-attested evidence

signed qualification result
  != verified result

verified result
  != lifecycle admission

Arena lift
  != qualification
```

Upstream labels may be inputs to policy and task design. They cannot satisfy physical execution, cleanup, reproduction, signing, trust, or admission gates.

## V card handling

A Notes v7 V card distinguishes:

```text
Expected Result
Observed Result
Verdict: PASS | FAIL | PARTIAL | NOT_RUN
Artifacts
Environment / Fixture
Limitations
```

Qualification rules:

1. `NOT_RUN` is a plan only and contributes no execution evidence.
2. A V card with PASS/FAIL/PARTIAL is accepted only as upstream provenance unless its artifact is independently bound to the exact Atlas handoff and declared qualification environment.
3. Local observation cannot be relabeled as sandbox execution.
4. A qualification run must execute the exact Skill digest, assertion digest, host, profile, policy, task pool, thresholds, environment, and nonce declared by the handoff.
5. All failed executions remain in the denominator and evidence bundle.
6. Cleanup/destruction proof and fresh-workspace reproduction remain mandatory where policy requires them.

## X conflict handling

An open or partially resolved X card is material qualification context when it affects:

```text
source Claim validity
Skill invariant
security boundary
host compatibility
expected oracle
task definition
license/freshness state
```

Required behavior:

- Preserve both claims and their Evidence IDs.
- Do not select a preferred claim merely to make the handoff executable.
- Reject or quarantine a handoff whose mandatory prerequisite remains `CONTESTED` without a declared policy treatment.
- Bind the exact conflict state and resolution policy into the handoff or policy digest when it affects execution.
- A later resolution changes an input digest and requires a new handoff and requalification.

## K knowledge-gap handling

A K card records an exact unknown, impact, evidence need, retrieval/test plan, unblock criteria, and priority.

Required behavior:

```text
CRITICAL/HIGH gap affecting a mandatory prerequisite
  -> qualification_eligible = false or explicit quarantine

MEDIUM/LOW gap outside the declared envelope
  -> preserve limitation and policy decision
```

The qualification executor must not fill a gap using model memory or silently broaden the task envelope. Closing a material K card changes upstream evidence/claim inputs and requires a new immutable handoff.

## Stable card identity

When an Atlas handoff includes v7 card provenance, it must preserve:

```text
stable_id
canonical_key
revision
lifecycle
content/source digest
```

Rules:

- Display aliases are not identity.
- Duplicate canonical keys or stable-ID collisions invalidate the provenance binding.
- `SUPERSEDED` and `FALSIFIED` cards remain historical but cannot be active prerequisites.
- A revision, lifecycle, source digest, claim set, or conflict/gap change requires a new handoff digest.

## Qualification evidence authority

The qualification result is based on physical execution artifacts, not card prose. Required evidence remains:

```text
exact immutable Atlas handoff
canonical Skill artifact digest
assertion-contract digest
host/profile/policy/task-pool/threshold/environment digests
execution receipt
command/tool log where allowed
mandatory assertion outcomes
security violation record
artifact manifest and result digest
cleanup/destruction proof
fresh-workspace reproduction evidence
nonce/replay binding
signature and signer key ID
```

A result may reference upstream card/claim IDs for provenance. It must never copy complete private note/source bodies.

## Signing boundary

The signer signs the exact canonical qualification result and immutable handoff binding.

It must fail closed on:

```text
handoff digest mismatch
Skill or assertion digest mismatch
host/profile/policy/task-pool/environment mismatch
expired/revoked/untrusted key
nonce reuse or replay
missing physical artifact
mandatory assertion failure
security_violations != 0
missing cleanup proof
insufficient reproduction
material X/K state omitted from declared inputs
private source/body leakage
```

Private keys are supplied only through approved external secret channels. They are never committed, printed, placed in fixtures, or uploaded as artifacts.

## Admission boundary

Qualification state transitions remain separate:

```text
physical execution complete
  -> signed result
  -> independent signature/digest verification
  -> qualification decision
  -> reviewed lifecycle admission
  -> production routing authority
  -> repository-specific implicit invocation policy
```

An upstream v7 protocol change, card revision, assertion change, source version change, conflict resolution, knowledge-gap closure, host adapter change, or security advisory may require requalification. This repository does not decide Notes Claim admission or Atlas Capability intent.

## Arena boundary

Arena evaluation may compare a candidate Skill against a no-Skill baseline using paired tasks and pinned environments. It must preserve failures and uncertainty.

Notes v7 fields may inform stratification or failure analysis, but they must not:

```text
change qualification state
remove failed runs
alter a preregistered task after observing outcomes
become a universal single score
expose private source/note content
```

## Current status

This document is an authority contract only. It does not prove that the Atlas v7 Drive-aware adapter, signed qualification round trip, production trust root, real OpenShell run, or lifecycle admission has been implemented. Those states require repository-local code, tests, physical evidence, signatures, verification, and landing authority.
