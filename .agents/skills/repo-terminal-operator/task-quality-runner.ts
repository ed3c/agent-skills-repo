import { createHash } from "node:crypto";
import {
  runOwnedProfileCommand,
  type OwnedProfileCommandResult,
} from "../../../../../skills/repo-neural-perception/scripts/owned-profile-command";
import {
  REPO_TERMINAL_CODE_QUALITY_COMMAND,
  TASK_QUALITY_STAGE_DEFINITIONS,
  taskQualityProfileSha256,
  type TaskQualityCwd,
  type TaskQualityStageDefinition,
} from "./task-quality-contract";

export type TaskQualityRoots = { adapter: string; workspace: string };
export type TaskQualityStageReceipt = {
  id: string;
  phase: number;
  cwd: TaskQualityCwd;
  command: string[];
  status: "passed" | "failed" | "blocked";
  exit_code: number | null;
  elapsed_ms: number;
  timed_out: boolean;
  cancelled: boolean;
  process_reaped: boolean;
  timer_cleared: boolean;
  stdout_consumed: boolean;
  stderr_consumed: boolean;
  cleanup_errors: string[];
  stdout_sha256: string | null;
  stderr_sha256: string | null;
  test_cases: number | null;
  diagnostic: string;
};

export type TaskQualityReceipt = {
  schema_version: "repo-terminal-code-quality-receipt@v1";
  status: "passed" | "failed";
  claim_boundary: "task-scoped-code-quality";
  profile_sha256: string;
  command: string[];
  stages: TaskQualityStageReceipt[];
  coverage: {
    status: "not-selected";
    next_mode: "production-use/writer-entrypoint";
  };
  elapsed_ms: number;
  exit_code: number;
  timed_out: boolean;
  cancelled: boolean;
  process_reaped: boolean;
  timer_cleared: boolean;
  stdout_consumed: boolean;
  stderr_consumed: boolean;
  cleanup_errors: string[];
  diagnostic: string;
};

export type TaskQualityCommandRunner = (
  command: string[],
  cwd: string,
  timeoutMs: number,
) => Promise<OwnedProfileCommandResult>;

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function testCases(output: string): number | null {
  const match = /(?:^|\n)\s*(\d+) pass\s*(?:\n|$)/u.exec(output);
  return match?.[1] ? Number(match[1]) : null;
}

function isTestStage(stage: TaskQualityStageDefinition): boolean {
  return stage.command[0] === "bun" && stage.command[1] === "test";
}

function clean(result: OwnedProfileCommandResult): boolean {
  return (
    result.exitCode === 0 &&
    !result.timedOut &&
    result.processReaped &&
    result.timerCleared &&
    result.stdoutConsumed &&
    result.stderrConsumed &&
    result.cleanupErrors.length === 0
  );
}

function progress(
  stage: TaskQualityStageDefinition,
  state: "started" | "finished",
  receipt?: TaskQualityStageReceipt,
): void {
  console.error(
    JSON.stringify({
      schema_version: "repo-terminal-task-quality-progress@v1",
      stage: stage.id,
      phase: stage.phase,
      state,
      ...(receipt
        ? {
            status: receipt.status,
            exit_code: receipt.exit_code,
            elapsed_ms: receipt.elapsed_ms,
          }
        : {}),
    }),
  );
}

async function runStage(
  stage: TaskQualityStageDefinition,
  roots: TaskQualityRoots,
  run: TaskQualityCommandRunner,
): Promise<TaskQualityStageReceipt> {
  progress(stage, "started");
  const started = performance.now();
  const result = await run(stage.command, roots[stage.cwd], stage.timeout_ms);
  const testStage = isTestStage(stage);
  const observedTestCases = testStage
    ? testCases(`${result.stdout}\n${result.stderr}`)
    : null;
  const passed = clean(result) && (!testStage || observedTestCases !== null);
  const receipt: TaskQualityStageReceipt = {
    id: stage.id,
    phase: stage.phase,
    cwd: stage.cwd,
    command: [...stage.command],
    status: passed ? "passed" : "failed",
    exit_code: result.exitCode,
    elapsed_ms: Math.round(performance.now() - started),
    timed_out: result.timedOut,
    cancelled: false,
    process_reaped: result.processReaped,
    timer_cleared: result.timerCleared,
    stdout_consumed: result.stdoutConsumed,
    stderr_consumed: result.stderrConsumed,
    cleanup_errors: [...result.cleanupErrors],
    stdout_sha256: sha256(result.stdout),
    stderr_sha256: sha256(result.stderr),
    test_cases: observedTestCases,
    diagnostic: passed
      ? "passed"
      : testStage && observedTestCases === null && clean(result)
        ? "test command reported no executed tests"
        : (
            result.stderr.trim() ||
            result.stdout.trim() ||
            `exit ${result.exitCode}`
          ).slice(-2_000),
  };
  progress(stage, "finished", receipt);
  return receipt;
}

function blocked(stage: TaskQualityStageDefinition): TaskQualityStageReceipt {
  return {
    id: stage.id,
    phase: stage.phase,
    cwd: stage.cwd,
    command: [...stage.command],
    status: "blocked",
    exit_code: null,
    elapsed_ms: 0,
    timed_out: false,
    cancelled: false,
    process_reaped: false,
    timer_cleared: false,
    stdout_consumed: false,
    stderr_consumed: false,
    cleanup_errors: [],
    stdout_sha256: null,
    stderr_sha256: null,
    test_cases: null,
    diagnostic: "blocked by an earlier phase",
  };
}

async function runPhase(
  definitions: readonly TaskQualityStageDefinition[],
  roots: TaskQualityRoots,
  run: TaskQualityCommandRunner,
): Promise<TaskQualityStageReceipt[]> {
  const settled = await Promise.allSettled(
    definitions.map((stage) => runStage(stage, roots, run)),
  );
  return settled.map((result, index) => {
    if (result.status === "fulfilled") return result.value;
    const stage = definitions[index];
    if (!stage) throw new Error("task-quality phase result index mismatch");
    const receipt: TaskQualityStageReceipt = {
      ...blocked(stage),
      status: "failed",
      cleanup_errors: [
        result.reason instanceof Error
          ? result.reason.message
          : String(result.reason),
      ],
      diagnostic: "stage runner rejected",
    };
    progress(stage, "finished", receipt);
    return receipt;
  });
}

export async function executeTaskQuality(
  roots: TaskQualityRoots,
  run: TaskQualityCommandRunner = runOwnedProfileCommand,
): Promise<TaskQualityReceipt> {
  const started = performance.now();
  const receipts: TaskQualityStageReceipt[] = [];
  for (const phase of [
    ...new Set(TASK_QUALITY_STAGE_DEFINITIONS.map((stage) => stage.phase)),
  ].sort()) {
    const definitions = TASK_QUALITY_STAGE_DEFINITIONS.filter(
      (stage) => stage.phase === phase,
    );
    if (receipts.some((receipt) => receipt.status !== "passed"))
      receipts.push(...definitions.map(blocked));
    else receipts.push(...(await runPhase(definitions, roots, run)));
  }
  const passed = receipts.every((receipt) => receipt.status === "passed");
  const cleanupErrors = receipts.flatMap((receipt) =>
    receipt.cleanup_errors.map((error) => `${receipt.id}: ${error}`),
  );
  return {
    schema_version: "repo-terminal-code-quality-receipt@v1",
    status: passed ? "passed" : "failed",
    claim_boundary: "task-scoped-code-quality",
    profile_sha256: taskQualityProfileSha256(),
    command: [...REPO_TERMINAL_CODE_QUALITY_COMMAND],
    stages: receipts,
    coverage: {
      status: "not-selected",
      next_mode: "production-use/writer-entrypoint",
    },
    elapsed_ms: Math.round(performance.now() - started),
    exit_code: passed ? 0 : 2,
    timed_out: receipts.some((receipt) => receipt.timed_out),
    cancelled: receipts.some((receipt) => receipt.cancelled),
    process_reaped: receipts
      .filter((receipt) => receipt.status !== "blocked")
      .every((receipt) => receipt.process_reaped),
    timer_cleared: receipts
      .filter((receipt) => receipt.status !== "blocked")
      .every((receipt) => receipt.timer_cleared),
    stdout_consumed: receipts
      .filter((receipt) => receipt.status !== "blocked")
      .every((receipt) => receipt.stdout_consumed),
    stderr_consumed: receipts
      .filter((receipt) => receipt.status !== "blocked")
      .every((receipt) => receipt.stderr_consumed),
    cleanup_errors: cleanupErrors,
    diagnostic: passed
      ? "task-scoped static and focused behavior gates passed"
      : "task-quality phase failed",
  };
}
