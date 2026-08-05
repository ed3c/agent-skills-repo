import { inspectAsyncProductionRun } from "./async-job-lifecycle";
import type { AsyncWorkerJob } from "./async-worker-carrier";
import { assertOnlyKeys, exactArray } from "./async-admission-contract";
import { assertPassingSmallLoopReceipt } from "./small-loop-receipt";
import { isAbsolute } from "node:path";

export function assertForegroundReceipt(
  receipt: Record<string, unknown>,
  runId: string,
  expectedHead: string,
): void {
  try {
    assertPassingSmallLoopReceipt(receipt, runId, expectedHead);
  } catch (error) {
    throw new Error("foreground-receipt-binding-invalid", { cause: error });
  }
}

export function assertWorkerResult(
  result: Record<string, unknown>,
  job: AsyncWorkerJob,
  view: ReturnType<typeof inspectAsyncProductionRun>,
  expectedHead: string,
): { isolationMode: string; degraded: boolean } {
  const source = result.source as Record<string, unknown> | undefined;
  const command = result.command as Record<string, unknown> | undefined;
  const executable = command?.executable as Record<string, unknown> | undefined;
  const driver = command?.driver as Record<string, unknown> | undefined;
  const isolation = result.isolation as Record<string, unknown> | undefined;
  const cleanup = result.cleanup as Record<string, unknown> | undefined;
  const reviewer = result.reviewer_final as Record<string, unknown> | undefined;
  const findings = exactArray(reviewer?.findings);
  assertWorkerResultFields(
    result,
    source,
    command,
    executable,
    driver,
    reviewer,
    isolation,
    cleanup,
    findings,
  );
  const blocker = findings.some(
    (finding) =>
      Boolean(finding) &&
      typeof finding === "object" &&
      !Array.isArray(finding) &&
      (finding as Record<string, unknown>).severity === "blocker",
  );
  const findingsValid =
    Array.isArray(reviewer?.findings) &&
    findings.every((finding) => {
      if (!finding || typeof finding !== "object" || Array.isArray(finding)) {
        return false;
      }
      const value = finding as Record<string, unknown>;
      return (
        ["blocker", "warning", "info"].includes(String(value.severity)) &&
        typeof value.code === "string" &&
        value.code.length > 0 &&
        typeof value.summary === "string" &&
        value.summary.length > 0
      );
    });
  const cleanupValid =
    cleanup?.process_reaped === true &&
    cleanup.timer_cleared === true &&
    cleanup.stdout_consumed === true &&
    cleanup.stderr_consumed === true &&
    cleanup.snapshot_removed === true &&
    Array.isArray(cleanup.cleanup_errors) &&
    cleanup.cleanup_errors.length === 0;
  const executablePath = executable?.path;
  const executableRealPath = executable?.real_path;
  const commandValid =
    Array.isArray(command?.argv) &&
    JSON.stringify(command.argv) === JSON.stringify(job.command) &&
    executablePath === job.command[0] &&
    typeof executableRealPath === "string" &&
    isAbsolute(executableRealPath) &&
    executable?.sha256 === job.executable_sha256 &&
    driver?.path === job.driver_path &&
    driver.sha256 === job.driver_sha256;
  const isolationMode =
    typeof isolation?.mode === "string" ? isolation.mode : "";
  const degraded = isolationMode === "cwd-only-degraded";
  const isolationValid =
    isolation?.admission_eligible === false &&
    isolation.live_repository_write_denied === true &&
    ((isolationMode === "darwin-sandbox" &&
      isolation.network_denied === true) ||
      (degraded && job.allow_degraded === true));
  if (
    result.schema_version !== "repo-async-production-worker-result@v1" ||
    result.status !== "verified" ||
    result.run_id !== view.runId ||
    result.seal_sha256 !== view.sealSha256 ||
    result.candidate_sha256 !== view.candidateSha256 ||
    result.production_job_sha256 !== view.bindings.productionJobSha256 ||
    result.result_ref !== view.resultRef ||
    source?.expected_head !== expectedHead ||
    source.current_head !== expectedHead ||
    source.binding !== "current" ||
    reviewer?.schema_version !==
      "gcr-sealed-background-review-stage-final@v1" ||
    reviewer.status !== "passed" ||
    typeof reviewer.summary !== "string" ||
    reviewer.summary.length === 0 ||
    !findingsValid ||
    blocker ||
    !cleanupValid ||
    !commandValid ||
    !isolationValid ||
    !Number.isSafeInteger(result.elapsed_ms) ||
    Number(result.elapsed_ms) < 0
  ) {
    throw new Error("worker-result-binding-invalid");
  }
  if (degraded) throw new Error("degraded-worker-not-admissible");
  return { isolationMode, degraded };
}

function assertWorkerResultFields(
  result: Record<string, unknown>,
  source: Record<string, unknown> | undefined,
  command: Record<string, unknown> | undefined,
  executable: Record<string, unknown> | undefined,
  driver: Record<string, unknown> | undefined,
  reviewer: Record<string, unknown> | undefined,
  isolation: Record<string, unknown> | undefined,
  cleanup: Record<string, unknown> | undefined,
  findings: unknown[],
): void {
  assertOnlyKeys(
    result,
    [
      "schema_version",
      "status",
      "run_id",
      "seal_sha256",
      "candidate_sha256",
      "production_job_sha256",
      "result_ref",
      "source",
      "command",
      "reviewer_final",
      "diagnostic",
      "isolation",
      "cleanup",
      "elapsed_ms",
    ],
    "worker-result",
  );
  assertOnlyKeys(
    source,
    ["expected_head", "current_head", "binding"],
    "worker-result-source",
  );
  assertOnlyKeys(
    command,
    ["argv", "executable", "driver"],
    "worker-result-command",
  );
  assertOnlyKeys(
    executable,
    ["path", "real_path", "sha256"],
    "worker-result-executable",
  );
  assertOnlyKeys(driver, ["path", "sha256"], "worker-result-driver");
  assertOnlyKeys(
    reviewer,
    ["schema_version", "status", "summary", "findings"],
    "worker-result-reviewer",
  );
  assertOnlyKeys(
    isolation,
    [
      "mode",
      "admission_eligible",
      "network_denied",
      "live_repository_write_denied",
    ],
    "worker-result-isolation",
  );
  assertOnlyKeys(
    cleanup,
    [
      "process_reaped",
      "timer_cleared",
      "stdout_consumed",
      "stderr_consumed",
      "snapshot_removed",
      "cleanup_errors",
    ],
    "worker-result-cleanup",
  );
  for (const finding of findings) {
    assertOnlyKeys(
      finding && typeof finding === "object" && !Array.isArray(finding)
        ? (finding as Record<string, unknown>)
        : undefined,
      ["severity", "code", "summary"],
      "worker-result-finding",
    );
  }
}
