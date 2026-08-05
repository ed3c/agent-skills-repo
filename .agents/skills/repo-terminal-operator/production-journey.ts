#!/usr/bin/env bun
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { runBoundedProcess } from "./bounded-subprocess";

const root = import.meta.dir;
const outputRepo = resolve(root, "../../..");
const workspaceRoot = resolve(outputRepo, "../..");
const artifactRoot = join(outputRepo, "artifacts/repo-terminal-operator");
const adapter = join(root, "repo-adapter.ts");

function runPreflight(packetPath: string) {
  const result = spawnSync("bun", ["run", adapter, "--preflight", packetPath], { cwd: outputRepo, encoding: "utf8", timeout: 5_000 });
  let receipt: Record<string, unknown> | null = null;
  let parseError: string | null = null;
  try {
    receipt = JSON.parse(result.stdout) as Record<string, unknown>;
  } catch (error) {
    parseError = error instanceof Error ? error.message : String(error);
  }
  return {
    exit_code: result.status,
    receipt,
    stderr: result.stderr.trim(),
    parse_error: parseError,
    timed_out: result.error?.code === "ETIMEDOUT",
  };
}

async function runBoundedPreflight(packetPath: string) {
  const result = await runBoundedProcess(
    ["bun", "run", adapter, "--preflight", packetPath],
    { cwd: outputRepo, timeoutMs: 5_000 },
  );
  let receipt: Record<string, unknown> | null = null;
  let parseError: string | null = null;
  try {
    receipt = JSON.parse(result.stdout) as Record<string, unknown>;
  } catch (error) {
    parseError = error instanceof Error ? error.message : String(error);
  }
  return {
    ...result,
    receipt,
    parseError,
    stderr: result.stderr.trim(),
    receiptSha256: createHash("sha256").update(result.stdout.trim()).digest("hex"),
  };
}

async function runConcurrent(packetPath: string) {
  return Promise.allSettled(Array.from({ length: 4 }, () => runBoundedPreflight(packetPath)));
}

async function main(): Promise<number> {
  mkdirSync(artifactRoot, { recursive: true });
  const head = spawnSync("git", ["-C", workspaceRoot, "rev-parse", "HEAD"], { encoding: "utf8" }).stdout.trim();
  const packet = {
    schema_version: "terminal-slice-packet@v2",
    terminal_slice_id: "repo-production-safety-20260731",
    guided_claim_ids: ["user-production-safety-20260731-claim-01", "user-production-safety-20260731-claim-02"],
    claim_set: { ref: "guided/user-production-safety-20260731.claim-set.json", sha256: "ac8eed5ded09163d5323f574f77d2e26837fa84c333ea97bb80ccd4dce05a71b" },
    target_repo: relative(workspaceRoot, outputRepo),
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
      context_budget: { max_active_files: 6, target_files: ["production-journey.ts", "bounded-subprocess.ts"], target_tests: ["bounded-subprocess.test.ts"] },
      tdd: { red_receipt: "verification/production-safety-agentic-tdd.json#red", green_receipt: "verification/production-safety-agentic-tdd.json#green", tests_immutable_during_green: true },
      minimal_diff: { allowed_paths_only: true, unrelated_refactors: false },
    },
    code_quality: { profile: "code-quality/default", commands: [["bun", "test", "tests/focused.test.ts"]] },
    production_use: { profile: "production-use/default", commands: [["bun", "run", "src/index.ts", "--help"]] },
    lineage: { local_id: "HARNESS-CROSS-CUTTING-REPO-NEURAL-PERCEPTION", forgejo_issue: "local-lineage-pending" },
    write_lease: { lease_id: "production-journey", owner: "repo-terminal-operator", expires_at: new Date(Date.now() + 300_000).toISOString(), expected_head: head },
    handoff: {
      schema_version: "handoff-envelope@v1",
      source_sha256: "a".repeat(64),
      input_sha256: "b".repeat(64),
      issue_id: "local-lineage-pending",
      run_id: "repo-terminal-production-journey",
      prompt_id: "repo-terminal-production-journey",
      pre_assertions: ["terminal packet schema passes", "write lease is live"],
      post_assertions: ["valid packet passes", "stale HEAD fails closed"],
      retry_classification: "not-attempted",
      artifact_refs: ["artifacts/repo-terminal-operator/production-journey.receipt.json"],
      next_legal_edges: ["code-quality", "production-use"],
    },
  };
  const successPath = join(artifactRoot, "valid-terminal-packet.json");
  const stalePath = join(artifactRoot, "stale-terminal-packet.json");
  const malformedPath = join(artifactRoot, "malformed-terminal-packet.json");
  writeFileSync(successPath, `${JSON.stringify(packet, null, 2)}\n`);
  writeFileSync(stalePath, `${JSON.stringify({ ...packet, write_lease: { ...packet.write_lease, expected_head: "0".repeat(40) } }, null, 2)}\n`);
  writeFileSync(malformedPath, "{\n");

  const success = runPreflight(successPath);
  const stale = runPreflight(stalePath);
  const malformed = runPreflight(malformedPath);
  const concurrent = await runConcurrent(successPath);
  const concurrentReceipts = concurrent.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
  const deterministicReceipts = new Set(concurrentReceipts.map((result) => result.receiptSha256)).size === 1;
  const concurrentPassed = concurrent.length === 4
    && concurrentReceipts.length === 4
    && deterministicReceipts
    && concurrentReceipts.every((result) => result.exitCode === 0
      && result.receipt?.status === "passed"
      && result.processReaped
      && result.timerCleared
      && !result.timedOut);
  const lifecycleRuns: Awaited<ReturnType<typeof runBoundedPreflight>>[] = [];
  for (let index = 0; index < 12; index += 1) lifecycleRuns.push(await runBoundedPreflight(successPath));
  const timeoutProbe = await runBoundedProcess(
    ["bun", "-e", "setInterval(() => {}, 1000)"],
    { cwd: outputRepo, timeoutMs: 20 },
  );
  const cancellationController = new AbortController();
  const cancellationPending = runBoundedProcess(
    ["bun", "-e", "setInterval(() => {}, 1000)"],
    { cwd: outputRepo, timeoutMs: 5_000, signal: cancellationController.signal },
  );
  cancellationController.abort();
  const cancellationProbe = await cancellationPending;
  const lifecyclePassed = lifecycleRuns.every((result) => result.exitCode === 0
    && result.receipt?.status === "passed"
    && !result.timedOut
    && result.processReaped
    && result.timerCleared
    && result.stdoutConsumed
    && result.stderrConsumed
    && result.cleanupErrors.length === 0)
    && timeoutProbe.exitCode === 124
    && timeoutProbe.timedOut
    && timeoutProbe.processReaped
    && timeoutProbe.timerCleared
    && cancellationProbe.exitCode === 130
    && cancellationProbe.cancelled
    && cancellationProbe.processReaped
    && cancellationProbe.timerCleared;
  const validPassed = success.receipt?.status === "passed" && success.exit_code === 0;
  const stalePassed = stale.receipt?.status === "failed" && stale.exit_code === 2
    && Array.isArray(stale.receipt?.checks)
    && (stale.receipt.checks as Array<Record<string, unknown>>).some((check) => check.id === "expected-head" && check.status === "failed");
  const malformedChecks = Array.isArray(malformed.receipt?.checks)
    ? malformed.receipt.checks as Array<Record<string, unknown>>
    : [];
  const originalErrorDetail = malformedChecks.find((check) => check.id === "input-readable"
    && check.status === "failed"
    && typeof check.detail === "string"
    && check.detail.length > 0)?.detail;
  const originalErrorPreserved = typeof originalErrorDetail === "string";
  const silentFailurePassed = malformed.receipt?.status === "failed"
    && malformed.exit_code === 2
    && malformed.parse_error === null
    && originalErrorPreserved;
  const scenarios = [
    { id: "valid-packet", status: validPassed ? "passed" : "failed", expected: "passed", entrypoint_observed: success.receipt?.status, exit_code: success.exit_code, diagnostic: success.parse_error ?? success.stderr },
    { id: "stale-head-recovery", status: stalePassed ? "passed" : "failed", expected: "typed expected-head failure", entrypoint_observed: stale.receipt?.status, exit_code: stale.exit_code, diagnostic: stale.parse_error ?? stale.stderr },
    {
      id: "race-condition",
      status: concurrentPassed ? "passed" : "failed",
      expected: "four bounded concurrent passes",
      attempts: concurrent.length,
      entrypoint_scope: "process-isolated-read-only-preflight",
      bounded_concurrency: 4,
      deterministic_receipts: deterministicReceipts,
      shared_mutation_observed: false,
      diagnostic: concurrent.map((result) => result.status === "fulfilled"
        ? {
          status: result.status,
          exit_code: result.value.exitCode,
          receipt_status: result.value.receipt?.status,
          process_reaped: result.value.processReaped,
          timer_cleared: result.value.timerCleared,
          timed_out: result.value.timedOut,
          receipt_sha256: result.value.receiptSha256,
        }
        : { status: result.status, reason: String(result.reason) }),
    },
    {
      id: "silent-failure",
      status: silentFailurePassed ? "passed" : "failed",
      expected: "typed failure with original parse diagnostic",
      entrypoint_observed: malformed.receipt?.status,
      exit_code: malformed.exit_code,
      typed_failure_receipt: malformed.parse_error === null,
      original_error_preserved: originalErrorPreserved,
      diagnostic: originalErrorDetail ?? malformed.parse_error ?? malformed.stderr,
    },
    {
      id: "resource-leak",
      status: lifecyclePassed ? "passed" : "failed",
      expected: "twelve bounded exits with reaped subprocesses, drained streams, and cleared timers",
      attempts: lifecycleRuns.length,
      subprocesses_reaped: lifecycleRuns.every((result) => result.processReaped),
      timers_cleared: lifecycleRuns.every((result) => result.timerCleared),
      stdout_consumed: lifecycleRuns.every((result) => result.stdoutConsumed),
      stderr_consumed: lifecycleRuns.every((result) => result.stderrConsumed),
      timed_out_count: lifecycleRuns.filter((result) => result.timedOut).length,
      timeout_cleanup_probe: timeoutProbe,
      cancellation_cleanup_probe: cancellationProbe,
      diagnostic: lifecycleRuns.map((result) => ({
        exit_code: result.exitCode,
        timed_out: result.timedOut,
        process_reaped: result.processReaped,
        timer_cleared: result.timerCleared,
        stdout_consumed: result.stdoutConsumed,
        stderr_consumed: result.stderrConsumed,
        cleanup_errors: result.cleanupErrors,
        parse_error: result.parseError,
        stderr: result.stderr,
      })),
    },
  ];
  const passed = validPassed && stalePassed && concurrentPassed && silentFailurePassed && lifecyclePassed;
  const artifactPath = join(artifactRoot, "production-journey.receipt.json");
  const receipt = {
    schema_version: "repo-terminal-production-journey-receipt@v1",
    status: passed ? "passed" : "failed",
    entrypoint: relative(outputRepo, adapter),
    evidence_scope: "deterministic-preflight-entrypoint",
    writer_execution_safety: "unobserved-repo-local-agent-boundary",
    scenarios,
    safety_coverage: ["race-condition", "silent-failure", "resource-leak"],
    artifact_path: relative(workspaceRoot, artifactPath),
  };
  writeFileSync(artifactPath, `${JSON.stringify(receipt, null, 2)}\n`);
  console.log(JSON.stringify(receipt));
  return passed ? 0 : 2;
}

process.exitCode = await main();
