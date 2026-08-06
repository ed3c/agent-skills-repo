---
name: "autoresearch-composer"
description: "Plan bounded metric-driven autoresearch loops without collapsing match, generate, and validate into one prompt. The asset prevents compressed context from becoming an ambiguous route decision. Use when: A task asks for Goal/Scope/Metric/Direction/Verify/Guard/Iterations and keep/discard optimization."
license: "MIT"
compatibility: "Portable Agent Skills export from agent-skills-repo. Host access is limited to the bundled instructions and resources; conformance does not imply qualification."
metadata:
  ed3c-export-schema: "agent-skills-export@1"
  ed3c-lifecycle-status: "production-seed-candidate"
  ed3c-production-routable: "false"
  ed3c-source-artifact-digest: "sha256:7178c399a57b93dca08d63e7d71047ff92033763466a4d0c79900bb0a0d3e5be"
  ed3c-source-skill-id: "autoresearch_composer"
  ed3c-source-version: "unversioned"
---
# Autoresearch Composer

WHY: Plan bounded metric-driven autoresearch loops without collapsing match, generate, and validate into one prompt. The asset prevents compressed context from becoming an ambiguous route decision.

HOW: Build a state graph, choose native-yield or slash route, generate an Iteration-Loop Contract, validate cases and A/B delta, and recover missing Domain terms via references/state_graph.md. Actor routing follows judge-loop-chooser: Opus/Codex/agy are assigned one evidence role each, never left as an unresolved choice. Lifecycle promotion also requires Golden Dataset evals, deterministic guardrails, pytest eval markers, local LLM-as-a-Judge heuristic verdicts, and local-first trace sampling; cloud/API judge hooks are present but disabled by default.

WHEN: A task asks for Goal/Scope/Metric/Direction/Verify/Guard/Iterations and keep/discard optimization.

WHEN NOT: General SDLC planning, TDD fixes, security reviews, bug diagnosis, design grilling, or family eval case authoring.
