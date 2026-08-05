import { mkdirSync, writeFileSync } from "node:fs";
import { join, relative } from "node:path";

const PACKET_BASE = {
  schema_version: "terminal-slice-packet@v2",
  terminal_slice_id: "repo-production-safety-20260731",
  guided_claim_ids: ["user-production-safety-20260731-claim-01", "user-production-safety-20260731-claim-02"],
  claim_set: { ref: "guided/user-production-safety-20260731.claim-set.json", sha256: "ac8eed5ded09163d5323f574f77d2e26837fa84c333ea97bb80ccd4dce05a71b" },
  operator_skill: ".agents/skills/repo-terminal-operator",
  entrypoint: ["bun", "run", ".agents/skills/repo-terminal-operator/repo-adapter.ts", "--preflight"],
  input_contract: "guided-claim-set@v1",
  output_contract: "small-loop-run-receipt@v1",
  allowed_paths: ["src", "tests"],
  risk: "low",
  agentic_execution: {
    objective: "Verify deterministic preflight production safety without claiming writer safety.",
    invariants: ["bounded concurrency", "typed failures", "deterministic cleanup"],
    public_contract: { input: "terminal-slice-packet@v2", output: "small-loop-run-receipt@v1" },
    failure_modes: ["stale head", "malformed input", "timeout", "cancellation"],
    context_budget: { max_active_files: 6, target_files: ["production-safety-journey.ts", "production-journey-scenarios.ts", "bounded-subprocess.ts"], target_tests: ["bounded-subprocess.test.ts"] },
    tdd: { red_receipt: "verification/production-safety-agentic-tdd.json#red", green_receipt: "verification/production-safety-agentic-tdd.json#green", tests_immutable_during_green: true },
    minimal_diff: { allowed_paths_only: true, unrelated_refactors: false },
  },
  code_quality: { profile: "code-quality/default", commands: [["bun", "test", "tests/focused.test.ts"]] },
  production_use: { profile: "production-use/default", commands: [["bun", "run", "src/index.ts", "--help"]] },
  lineage: { local_id: "HARNESS-CROSS-CUTTING-REPO-NEURAL-PERCEPTION", forgejo_issue: "local-lineage-pending" },
  handoff: {
    schema_version: "handoff-envelope@v1",
    source_sha256: "a".repeat(64), input_sha256: "b".repeat(64),
    issue_id: "local-lineage-pending", run_id: "repo-terminal-production-journey", prompt_id: "repo-terminal-production-journey",
    pre_assertions: ["terminal packet schema passes", "write lease is live"],
    post_assertions: ["valid packet passes", "stale HEAD fails closed"],
    retry_classification: "not-attempted",
    artifact_refs: ["artifacts/repo-terminal-operator/production-journey.receipt.json"],
    next_legal_edges: ["code-quality", "production-use"],
  },
};

function packet(head: string, workspaceRoot: string, outputRepo: string) {
  return {
    ...PACKET_BASE,
    target_repo: relative(workspaceRoot, outputRepo),
    write_lease: {
      lease_id: "production-journey", owner: "repo-terminal-operator",
      expires_at: new Date(Date.now() + 300_000).toISOString(), expected_head: head,
    },
  };
}

export function writeJourneyFixtures(artifactRoot: string, workspaceRoot: string, outputRepo: string, head: string) {
  mkdirSync(artifactRoot, { recursive: true });
  const valid = packet(head, workspaceRoot, outputRepo);
  const paths = {
    success: join(artifactRoot, "valid-terminal-packet.json"),
    stale: join(artifactRoot, "stale-terminal-packet.json"),
    malformed: join(artifactRoot, "malformed-terminal-packet.json"),
  };
  writeFileSync(paths.success, `${JSON.stringify(valid, null, 2)}\n`);
  writeFileSync(paths.stale, `${JSON.stringify({ ...valid, write_lease: { ...valid.write_lease, expected_head: "0".repeat(40) } }, null, 2)}\n`);
  writeFileSync(paths.malformed, "{\n");
  return paths;
}
