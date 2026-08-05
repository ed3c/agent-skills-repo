# Files

- [Ablation and Benchmark](ablation-and-benchmark.md) - The simulated ablation engine, the real-driver ablation that actually calls an agent, and the benchmark matrix — with the boundary between simulated and real evidence stated explicitly.
- [Behavioral Eval and Judge](behavioral-eval-and-judge.md) - The golden-dataset eval harness, its deterministic guardrails, the local heuristic judge, the double-lock verdict parser, and the fact that the cloud judge makes no API call.
- [Semantic Arbitration](semantic-arbitration.md) - How semantic claims stay candidate until human admit, why a review that did not run is surfaced as a gate rather than inferred as success, and what the agy execution profile is for.
- [Static Skill Validators](static-skill-validators.md) - The five non-behavioral validators, the behavior-preserving no-op purger, and which of them prove only their selftest under git_gate.
- [Synthetic Corpus Quality](synthetic-corpus-quality.md) - Why a 117/117 green canary coexists with quality_status=insufficient, and what the quality report measures that the canary cannot.
