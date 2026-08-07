# Replicated hard gates for stochastic qualification cases

Issue #35 established that a fixed skill, model, fixture, and execution envelope
can produce mixed oracle verdicts across independent draws. A single-shot result
therefore cannot distinguish an admissible artifact from luck.

## Adopted v1 policy

New qualification runs use `qualification-run@2` and the machine-readable policy
at `data/qualification/hard-gate-repetition-policy-v1.json`.

- Run every case **five** times under the same pinned envelope, with a fresh
  sandbox and attempt identity each time.
- Critical and anchor cases are deterministic-eligibility cases:
  - five passes: eligible for the group gate;
  - five failures: stable candidate failure;
  - any mixed verdict: `*_case_unstable`; the run is invalid and the case must
    be retired or repaired before another qualification attempt.
- Target cases are stochastic-rate cases. Their equal-sized repetition sets are
  pooled and compared with the separately frozen integer ppm threshold.
- LLM judgment remains `advisory_only` and cannot override any physical or
  deterministic failure.

Five repetitions are an operational v1 floor, not proof of determinism. A
50%-pass process clears five all-pass draws with probability 3.125%, which is
materially safer than one draw while keeping the first qualification tractable.
Arena ranking policy may require more repetitions.

## Cost coupling

Cost is priced by **metered attempts**, not unique cases. For `N` cases and five
repetitions, the budget must declare `5N` judged attempts. The CLI requires the
budget's judged-attempt count and refuses before writing a result when it differs
from the gate matrix.

This removes the sample-size mismatch class exposed by #28 and prevents a future
budget from pricing one sweep while execution performs five.

## Compatibility

The existing `evaluate_hard_gates` single-shot function and all already-landed
run documents retain their original meaning. The new result always records:

```text
legacy_runs_reinterpreted: false
```

No historical refusal or pass is recalculated under this policy. Adoption is
forward-only through `qualification-run@2`.

## CLI

```sh
python scripts/check_replicated_hard_gates.py \
  --policy data/qualification/hard-gate-repetition-policy-v1.json \
  --rows /path/to/attempts.json \
  --target-success-threshold-ppm 900000 \
  --budget-judged-attempt-count 60 \
  --output /path/to/hard-gate-result.json
```

Exit codes:

- `0`: all gates passed;
- `1`: a valid repeated-draw matrix was evaluated and refused;
- `2`: policy, matrix, or cost-basis evidence was inadmissible.

Run the positive and instability controls with:

```sh
python scripts/check_replicated_hard_gates.py --selftest
```
