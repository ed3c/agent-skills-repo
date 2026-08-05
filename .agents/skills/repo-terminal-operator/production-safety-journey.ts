#!/usr/bin/env bun
import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { writeJourneyFixtures } from "./production-journey-fixture";
import { runCleanupProbes, runConcurrent, runLifecycle, runPreflight } from "./production-journey-observer";
import { evaluateJourney } from "./production-journey-scenarios";
import { launchNativeEscapeProbe } from "./production-native-escape";

const root = import.meta.dir;
const outputRepo = resolve(root, "../../..");
const workspaceRoot = resolve(outputRepo, "../..");
const runId = process.env.SKILL_BETTOR_PRODUCTION_RUN_ID;
if (!runId || !/^[a-f0-9-]{36}$/u.test(runId)) throw new Error("production journey requires a carrier-owned run id");
const artifactBase = process.env.SKILL_BETTOR_CONTAINER_ARTIFACT_ROOT ?? join(outputRepo, "artifacts/repo-terminal-operator");
const artifactRoot = join(artifactBase, runId);
const adapter = join(root, "repo-adapter.ts");

async function observe(paths: ReturnType<typeof writeJourneyFixtures>) {
  const success = runPreflight(adapter, outputRepo, paths.success);
  const stale = runPreflight(adapter, outputRepo, paths.stale);
  const malformed = runPreflight(adapter, outputRepo, paths.malformed);
  const concurrent = await runConcurrent(adapter, outputRepo, paths.success);
  const lifecycle = await runLifecycle(adapter, outputRepo, paths.success);
  const probes = await runCleanupProbes(outputRepo);
  return { success, stale, malformed, concurrent, lifecycle, ...probes };
}

async function emitReceipt(evaluation: ReturnType<typeof evaluateJourney>): Promise<number> {
  const artifactPath = join(artifactRoot, "production-journey.receipt.json");
  const nativeEscapeProbeReady = await launchNativeEscapeProbe();
  const receipt = {
    schema_version: "repo-terminal-production-journey-receipt@v1",
    status: evaluation.passed ? "passed" : "failed",
    entrypoint: relative(outputRepo, adapter),
    evidence_scope: "deterministic-preflight-entrypoint",
    writer_execution_safety: "unobserved-repo-local-agent-boundary",
    scenarios: evaluation.scenarios,
    safety_coverage: ["race-condition", "silent-failure", "resource-leak"],
    native_escape_probe_launched: nativeEscapeProbeReady,
    native_escape_probe_ready: nativeEscapeProbeReady,
    artifact_path: relative(workspaceRoot, artifactPath),
  };
  const artifactBytes = `${JSON.stringify(receipt, null, 2)}\n`;
  writeFileSync(artifactPath, artifactBytes);
  console.log(JSON.stringify({
    ...receipt,
    artifact_sha256: createHash("sha256").update(artifactBytes).digest("hex"),
  }));
  return evaluation.passed ? 0 : 2;
}

async function main(): Promise<number> {
  const head = spawnSync("git", ["-C", workspaceRoot, "rev-parse", "HEAD"], { encoding: "utf8" }).stdout.trim();
  const paths = writeJourneyFixtures(artifactRoot, workspaceRoot, outputRepo, head);
  return emitReceipt(evaluateJourney(await observe(paths)));
}

process.exitCode = await main();
