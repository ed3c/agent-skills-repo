# Files

- [Async Lifecycle](async-lifecycle.md) - The seal, claim, finish, cancel and projection rules of the async production lifecycle — the exact CAS, lease, deadline and fencing checks, the control-plane disposition matrix, and which component actually executes work.
- [Evidence Cost](evidence-cost.md) - The cost cache and collector, their hash-bound request flow, the axis semantics that refuse to report a measured zero, and the unclosed v1/v2 handoff between them.
- [Terminal Operator Overview](overview.md) - The vendored Bun/TypeScript terminal-slice operator — packet contract and v1/v2 preflight, the phased task-quality gate and its test-count rule, the typed-receipt rejection rules, and the external dependencies that make this copy unrunnable.
- [Production Profiles and Handoff](production-profiles-and-handoff.md) - Which production journey can actually admit writer safety, why the local one cannot, and the exact boundary of the Forgejo git handoff.
- [Writer Publication](writer-publication.md) - publishWriterArtifact — the anchored, symlink-safe, crash-recoverable persistence core nine modules depend on, its exact outcome states, lock budget, recovery classification and failure taxonomy.
