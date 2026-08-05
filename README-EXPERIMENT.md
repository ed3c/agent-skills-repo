# This repository is the target of a documentation experiment

You are looking at a **generated repository** that was used as the subject of an experiment in
making generated code wikis checkable. The experiment, its five wiki arms, its harness and its
results live in a separate repository:

**→ [ed3c/openwiki-source-anchoring](https://github.com/ed3c/openwiki-source-anchoring)**

This one exists so that repository's numbers can be recomputed rather than taken on trust. Without
the target, its gate can be read but not run against real content.

## What this repository is

A skill-asset governance seed: about thirty Python gate scripts under `scripts/`, the data they
check under `data/`, two skill assets under `skills/`, and a test suite. `git push` runs a chain of
gates through `scripts/git_gate.py`.

Its own `README.md` describes it on its own terms. This file only explains its role in the
experiment.

**It was itself generated**, from a plan package, by the same system that later documented it. That
is the single most important limitation of every result measured against it: the pipeline under
test was reading something its own author had produced. Nothing measured here extrapolates to a
repository with a decade of organic history, dead code and three languages.

## What `openwiki/` is

`openwiki/` holds a wiki produced by a host-native port of
[langchain-ai/openwiki](https://github.com/langchain-ai/openwiki)'s `code`-mode pipeline, run with
**no API key** — official prompts, official review subagents, executed inside a CLI subscription
session rather than through the upstream binary.

Two parts, and the distinction matters for every number in the experiment:

| | what it is | in the experiment |
|---|---|---|
| `openwiki/nonofficial/` | 14 **hand-written** pages that predate the pipeline | a **constant**, present in every arm, excluded from every metric |
| everything else under `openwiki/` | the pipeline's own output | this is **arm A**, the baseline |

The hand-written pages cannot be removed: `scripts/check_openwiki.py` requires them by path and by
exact literal content in 28 places, `scripts/check_plan_package_compat.py` in 12 more, and
`test_skill_asset_structure` asserts their presence. They also cannot be counted, because scoring a
pipeline against pages it did not write is a category error. So they stay on disk and come out of
the denominator — **the measured set and the delivered set are different sets.**

## Its wiki says things about it that are wrong

The anchoring experiment found **53 statements in `openwiki/` that contradict this repository's
source**, of which 22 were later re-adjudicated blind by agents reading only source: 21 upheld,
none overturned. One miscount had propagated to ten pages.

Two live defects the experiment surfaced and did **not** fix, because a documentation run does not
modify source:

- `scripts/git_gate.py`'s `GATES` list has 22 entries while
  `scripts/check_plan_package_compat.py`'s `GIT_GATE_ORDER` has 23, so the `--gate-receipt` fast
  path cannot accept any receipt this repository's own gate produces.
- `scripts/check_openwiki.py` pins `protected_history=157` where the ledger and its verification
  run say 235.

They are left in place deliberately. Fixing them would change the target under the arms already
measured against it.

## Using it

```sh
git clone https://github.com/ed3c/openwiki-source-anchoring
git clone https://github.com/ed3c/agent-skills-repo

cd openwiki-source-anchoring
sh harness/selftest.sh                                   # the gate must catch a hollow anchor first
bun run harness/src/audit_wiki.ts wiki/arm-d-gate-driven ../agent-skills-repo --exclude nonofficial
```

Every anchor-rate, coverage and validity figure published there is recomputable this way. How to
read those numbers — and which of them are process metrics that should not be read as quality — is
in that repository's
[`docs/`](https://github.com/ed3c/openwiki-source-anchoring/tree/main/docs).

## Desensitisation

Absolute machine paths are replaced with `<host-repo>`, `<target-repo>` and `<home>` throughout.
**The identical replacement was applied to the wiki arms**, so anchors still resolve: the gate
checks that a quoted string occurs verbatim in the named file, and both sides were transformed the
same way. The published pair is a consistent desensitised projection of what was measured, and the
gate passing on it is the proof — 0 invalid anchors on four of five arms, and on the fifth only the
two circular-evidence defects that were reported as findings from the start.

Two directories are withheld: `.openwiki-review/`, which holds one arm's review transcripts and
whose presence would let a future run cite its own predecessor as evidence, and `__pycache__`.
