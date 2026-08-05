import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runOwnedProfileCommand } from "../../../../../skills/repo-neural-perception/scripts/owned-profile-command";
import { runBoundedProcess, type BoundedProcessResult } from "./bounded-subprocess";
import { runPreflightChecks, type PreflightCheck } from "./repo-preflight";
import { gateReceiptErrors, loadGateProfile, type GateProfile } from "./small-loop-gate-contract";
import {
  buildReceipt, preflightFailures, type Failure, type GateExecution, type SmallLoopReceipt,
  type Stage, type StageEvidence, type TerminalPacket,
} from "./small-loop-receipt";

export type { SmallLoopReceipt } from "./small-loop-receipt";
type FailureKind = Failure["kind"];

export type SmallLoopDependencies = {
  now(): Date;
  head(): Promise<string>;
  changedPaths(paths: string[]): Promise<string[]>;
  preflight(inputPath: string): PreflightCheck[];
  run(command: string[], timeoutMs: number): Promise<BoundedProcessResult>;
  validateReceipt?(name: "code-quality" | "production-use", result: BoundedProcessResult): string[];
};

const operatorRoot = import.meta.dir;
const outputRepo = resolve(operatorRoot, "../../..");
const workspaceRoot = resolve(outputRepo, "../..");

function parsePacket(inputPath: string): TerminalPacket {
  const packet = JSON.parse(readFileSync(inputPath, "utf8")) as Partial<TerminalPacket>;
  if (!packet.terminal_slice_id || !Array.isArray(packet.allowed_paths) || !packet.write_lease?.expected_head
      || !packet.write_lease.expires_at || !packet.handoff?.run_id
      || !Array.isArray(packet.code_quality?.commands) || !Array.isArray(packet.production_use?.commands)) {
    throw new Error("terminal packet is missing runner fields");
  }
  return packet as TerminalPacket;
}

function clean(result: BoundedProcessResult): boolean {
  return result.exitCode === 0 && !result.timedOut && !result.cancelled && result.processReaped
    && result.timerCleared && result.stdoutConsumed && result.stderrConsumed && result.cleanupErrors.length === 0;
}

function failureKind(result: BoundedProcessResult): FailureKind {
  return result.timedOut || result.cancelled ? "transient" : result.cleanupErrors.length > 0 ? "policy" : "deterministic";
}

function failureMessage(result: BoundedProcessResult): string {
  const diagnostic = result.stderr.trim() || result.stdout.trim() || `exit code ${result.exitCode}`;
  return diagnostic.slice(0, 2048);
}

async function runGate(
  name: "code-quality" | "production-use",
  gateProfile: GateProfile,
  dependencies: SmallLoopDependencies,
): Promise<{ stage: Stage; evidence: StageEvidence; failure?: Failure }> {
  const started = dependencies.now().getTime();
  const command = gateProfile.commands[0]!;
  const result = await dependencies.run(command, gateProfile.command_timeout_ms);
  const elapsed = Math.max(0, dependencies.now().getTime() - started);
  const processPassed = clean(result);
  const receiptErrors = processPassed
    ? (dependencies.validateReceipt ?? ((kind, observed) => gateReceiptErrors(kind, observed, workspaceRoot)))(name, result)
    : [];
  const passed = processPassed && receiptErrors.length === 0;
  return {
    stage: { name, ecosystem: "bun", purpose: name, status: passed ? "passed" : "failed", attempts: 1,
      inclusive_ms: elapsed, exclusive_ms: elapsed, cache: "miss" },
    evidence: { command, result },
    failure: passed ? undefined : { stage: name, kind: processPassed ? "policy" : failureKind(result),
      message: processPassed ? receiptErrors.join("; ").slice(0, 2048) : failureMessage(result) },
  };
}

function blockedStage(name: "code-quality" | "production-use"): Stage {
  return { name, ecosystem: "bun", purpose: name, status: "blocked", attempts: 0,
    inclusive_ms: 0, exclusive_ms: 0, cache: "not-applicable" };
}

async function executeGates(
  passed: boolean,
  inputPath: string,
  packet: TerminalPacket,
  dependencies: SmallLoopDependencies,
): Promise<GateExecution> {
  if (!passed) return {
    stages: [blockedStage("code-quality"), blockedStage("production-use")], failures: [],
    codeQualityEvidence: null, productionUseEvidence: null,
  };
  const codeQuality = await runGate("code-quality", loadGateProfile(operatorRoot, "code-quality.profile.json", packet.code_quality.commands), dependencies);
  const failures = codeQuality.failure ? [codeQuality.failure] : [];
  if (codeQuality.stage.status !== "passed") return {
    stages: [codeQuality.stage, blockedStage("production-use")], failures,
    codeQualityEvidence: codeQuality.evidence, productionUseEvidence: null,
  };
  const productionAdmission = dependencies.preflight(inputPath);
  const blocked = preflightFailures(productionAdmission);
  if (blocked.length > 0) return {
    stages: [codeQuality.stage, blockedStage("production-use")],
    failures: blocked.map((failure) => ({ ...failure, stage: "production-admission" })),
    codeQualityEvidence: codeQuality.evidence, productionUseEvidence: null,
  };
  const productionUse = await runGate("production-use", loadGateProfile(operatorRoot, "production-use.profile.json", packet.production_use.commands), dependencies);
  if (productionUse.failure) failures.push(productionUse.failure);
  return {
    stages: [codeQuality.stage, productionUse.stage], failures,
    codeQualityEvidence: codeQuality.evidence, productionUseEvidence: productionUse.evidence,
  };
}

function defaultDependencies(): SmallLoopDependencies {
  return {
    now: () => new Date(),
    head: async () => {
      const result = await runBoundedProcess(["git", "rev-parse", "HEAD"], { cwd: workspaceRoot, timeoutMs: 2_000 });
      if (!clean(result)) throw new Error(`cannot observe Git HEAD: ${failureMessage(result)}`);
      return result.stdout.trim();
    },
    changedPaths: async (paths) => {
      const result = await runBoundedProcess(["git", "status", "--porcelain=v1", "--untracked-files=all", "--", ...paths], {
        cwd: outputRepo, timeoutMs: 2_000,
      });
      if (!clean(result)) throw new Error(`cannot observe changed paths: ${failureMessage(result)}`);
      return result.stdout.trim().split(/\r?\n/u).filter(Boolean).map((line) => (line.slice(3).split(" -> ").at(-1) ?? "").trim());
    },
    preflight: (inputPath) => runPreflightChecks(inputPath, workspaceRoot, outputRepo),
    run: runOwnedGate,
  };
}

export async function runOwnedGate(command: string[], timeoutMs: number): Promise<BoundedProcessResult> {
  return { ...(await runOwnedProfileCommand(command, outputRepo, timeoutMs)), cancelled: false };
}

export async function runSmallLoop(
  inputPath: string,
  dependencies: SmallLoopDependencies = defaultDependencies(),
): Promise<SmallLoopReceipt> {
  const packet = parsePacket(inputPath);
  const startedAt = dependencies.now();
  const expectedHead = packet.write_lease.expected_head;
  const initialHead = await dependencies.head();
  const preflightStarted = dependencies.now().getTime();
  const preflight = dependencies.preflight(inputPath);
  const preflightElapsed = Math.max(0, dependencies.now().getTime() - preflightStarted);
  const preflightPassed = preflight.every((check) => check.status === "passed");
  const gates = await executeGates(preflightPassed, inputPath, packet, dependencies);
  const failures = [...preflightFailures(preflight), ...gates.failures];
  if (initialHead !== expectedHead) failures.push({ stage: "git-head", kind: "stale", message: `${expectedHead} -> ${initialHead}` });

  const actualHead = await dependencies.head();
  if (actualHead !== expectedHead) failures.push({ stage: "git-head", kind: "stale", message: `${expectedHead} -> ${actualHead}` });
  return buildReceipt({ inputPath, packet, startedAt, expectedHead, actualHead,
    changedPaths: await dependencies.changedPaths(packet.allowed_paths), preflight, preflightElapsed,
    gates, failures, completedAt: dependencies.now() });
}
