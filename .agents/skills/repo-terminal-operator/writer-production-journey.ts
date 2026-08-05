#!/usr/bin/env bun
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { runBoundedProcess } from "./bounded-subprocess";
import { launchNativeEscapeProbe } from "./production-native-escape";
import { writeWriterJourneyFixtures } from "./writer-production-fixture";
import type { WriterJourneyPaths } from "./writer-production-fixture";
import {
  observeConcurrentWriters,
  observeStageKill,
  observeWriter,
  publishedOutput,
  writerResidue,
} from "./writer-production-observer";
import { writerRaceScenario } from "./writer-production-race-scenario";
import { writerResourceScenario, writerSilentFailureScenario } from "./writer-production-scenarios";

const operatorRoot = import.meta.dir;
const outputRepo = resolve(operatorRoot, "../../..");
const workspaceRoot = resolve(outputRepo, "../..");
const writerEntrypoint = join(operatorRoot, "writer-entrypoint.ts");

async function cleanupProbes() {
  const timeout = await runBoundedProcess(["bun", "-e", "setInterval(() => {}, 1000)"], { cwd: outputRepo, timeoutMs: 20 });
  const controller = new AbortController();
  const pending = runBoundedProcess(["bun", "-e", "setInterval(() => {}, 1000)"], {
    cwd: outputRepo, timeoutMs: 5_000, signal: controller.signal,
  });
  controller.abort();
  return { timeout, cancellation: await pending };
}

async function recoveryObservations(paths: WriterJourneyPaths) {
  const rollbackKill = await observeStageKill(writerEntrypoint, outputRepo, paths, paths.rollbackOutput, "writer-rollback-crash", "output-linked");
  const rollbackRecovery = await observeWriter(writerEntrypoint, outputRepo, paths, paths.candidate, paths.rollbackOutput, "writer-rollback-recovery");
  const cancellationKill = await observeStageKill(writerEntrypoint, outputRepo, paths, paths.cancellationOutput, "writer-cancellation-crash", "pending-written");
  const cancellationAbsentBeforeRecovery = !existsSync(join(paths.root, paths.cancellationOutput));
  const cancellationRecovery = await observeWriter(writerEntrypoint, outputRepo, paths, paths.candidate, paths.cancellationOutput, "writer-cancellation-recovery");
  return {
    rollbackKill, rollbackRecovery, cancellationKill, cancellationRecovery, cancellationAbsentBeforeRecovery,
    rollbackResidue: writerResidue(paths, paths.rollbackOutput),
    rollbackPreserved: createHash("sha256").update(publishedOutput(paths, paths.rollbackOutput)).digest("hex") === paths.outputSha256,
    cancellationResidue: writerResidue(paths, paths.cancellationOutput),
  };
}

async function lifecycleObservations(paths: WriterJourneyPaths) {
  const lifecycle = [];
  for (let index = 0; index < 12; index += 1) {
    lifecycle.push(await observeWriter(writerEntrypoint, outputRepo, paths, paths.candidate, paths.raceOutput, `writer-lifecycle-${index}`));
  }
  return lifecycle;
}

async function observe(root: string) {
  const paths = writeWriterJourneyFixtures(root, workspaceRoot);
  const concurrent = await observeConcurrentWriters(writerEntrypoint, outputRepo, paths);
  const conflict = await observeWriter(writerEntrypoint, outputRepo, paths, paths.conflictCandidate, paths.raceOutput, "writer-conflict");
  const recovery = await recoveryObservations(paths);
  const lifecycle = await lifecycleObservations(paths);
  const malformed = await observeWriter(writerEntrypoint, outputRepo, paths, paths.missingCandidate, paths.raceOutput, "writer-malformed");
  const cleanup = await cleanupProbes();
  return { paths, concurrent, conflict, ...recovery, lifecycle, malformed, cleanup };
}

type Observed = Awaited<ReturnType<typeof observe>>;

function buildReceipt(observed: Observed, artifactRoot: string, nativeEscapeProbeReady: boolean) {
  const race = writerRaceScenario(observed);
  const silent = writerSilentFailureScenario(observed.malformed);
  const resource = writerResourceScenario(observed.lifecycle, observed.cleanup.timeout, observed.cleanup.cancellation);
  return {
    schema_version: "repo-terminal-writer-production-journey-receipt@v1",
    status: race.passed && silent.passed && resource.passed ? "passed" : "failed",
    entrypoint: relative(outputRepo, writerEntrypoint), evidence_scope: "writer-entrypoint",
    writer_execution_safety: "observed-writer-entrypoint",
    safety_coverage: ["race-condition", "silent-failure", "resource-leak"],
    source_binding: {
      source_manifest_ref: "sources/manifests/user-production-safety-20260731.json",
      source_sha256: observed.paths.sourceSha256,
      claim_set_ref: "guided/user-production-safety-20260731.claim-set.json",
      claim_set_sha256: observed.paths.claimSetSha256, output_sha256: observed.paths.outputSha256,
    },
    scenarios: [race.scenario, silent.scenario, resource.scenario],
    native_escape_probe_launched: nativeEscapeProbeReady, native_escape_probe_ready: nativeEscapeProbeReady,
    artifact_path: relative(workspaceRoot, join(artifactRoot, "writer-production-journey.receipt.json")),
  };
}

function persistReceipt(artifactRoot: string, receipt: ReturnType<typeof buildReceipt>): void {
  const path = join(artifactRoot, "writer-production-journey.receipt.json");
  const bytes = `${JSON.stringify(receipt, null, 2)}\n`;
  writeFileSync(path, bytes);
  if (!readFileSync(path).equals(Buffer.from(bytes))) throw new Error("writer journey receipt failed physical reopen");
}

async function main(): Promise<number> {
  const runId = process.env.SKILL_BETTOR_PRODUCTION_RUN_ID;
  if (!runId) throw new Error("writer journey requires a carrier-owned run id");
  const base = process.env.SKILL_BETTOR_CONTAINER_ARTIFACT_ROOT ?? join(outputRepo, "artifacts/repo-terminal-operator");
  const artifactRoot = join(base, runId);
  mkdirSync(artifactRoot, { recursive: true });
  const observed = await observe(artifactRoot);
  const nativeEscapeProbeReady = await launchNativeEscapeProbe();
  const receipt = buildReceipt(observed, artifactRoot, nativeEscapeProbeReady);
  persistReceipt(artifactRoot, receipt);
  console.log(JSON.stringify(receipt));
  return receipt.status === "passed" ? 0 : 2;
}

process.exitCode = await main();
