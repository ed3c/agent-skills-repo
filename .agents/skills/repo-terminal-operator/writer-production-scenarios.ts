import type { BoundedProcessResult } from "./bounded-subprocess";
import type { WriterObservation } from "./writer-production-observer";

type RecordValue = Record<string, unknown>;

export function cleanProcess(process: BoundedProcessResult): boolean {
  return process.processReaped && process.timerCleared && process.stdoutConsumed && process.stderrConsumed
    && process.cleanupErrors.length === 0 && !process.timedOut;
}

export function diagnostic(observation: WriterObservation): RecordValue {
  return {
    exit_code: observation.process.exitCode, receipt_status: observation.receipt?.status,
    process_reaped: observation.process.processReaped, timer_cleared: observation.process.timerCleared,
    timed_out: observation.process.timedOut, stdout_consumed: observation.process.stdoutConsumed,
    stderr_consumed: observation.process.stderrConsumed, cleanup_errors: observation.process.cleanupErrors,
    artifact_id: observation.receipt?.artifact_id, run_id: observation.receipt?.run_id,
    output_sha256: observation.receipt?.output_sha256, writer_outcome: observation.receipt?.writer_outcome,
    recovery_outcome: observation.receipt?.recovery_outcome,
  };
}

export function writerSilentFailureScenario(observation: WriterObservation) {
  const receipt = observation.receipt;
  const passed = observation.process.exitCode === 2 && cleanProcess(observation.process)
    && receipt?.status === "failed" && receipt.typed_failure_receipt === true
    && receipt.original_error_preserved === true && typeof receipt.diagnostic === "string";
  return { passed, scenario: {
    id: "silent-failure", status: passed ? "passed" : "failed", exit_code: observation.process.exitCode,
    entrypoint_observed: receipt?.status, typed_failure_receipt: receipt?.typed_failure_receipt,
    original_error_preserved: receipt?.original_error_preserved,
    diagnostic: receipt?.diagnostic ?? observation.process.stderr,
  } };
}

export function writerResourceScenario(
  lifecycle: WriterObservation[],
  timeout: BoundedProcessResult,
  cancellation: BoundedProcessResult,
) {
  const cleanRuns = lifecycle.every((item) => item.process.exitCode === 0 && cleanProcess(item.process));
  const cleanProbe = (probe: BoundedProcessResult) => probe.processReaped && probe.timerCleared
    && probe.stdoutConsumed && probe.stderrConsumed && probe.cleanupErrors.length === 0;
  const passed = lifecycle.length === 12 && cleanRuns && timeout.exitCode === 124 && timeout.timedOut
    && cancellation.exitCode === 130 && cancellation.cancelled && cleanProbe(timeout) && cleanProbe(cancellation);
  return { passed, scenario: {
    id: "resource-leak", status: passed ? "passed" : "failed", attempts: lifecycle.length,
    subprocesses_reaped: lifecycle.every((item) => item.process.processReaped),
    timers_cleared: lifecycle.every((item) => item.process.timerCleared),
    stdout_consumed: lifecycle.every((item) => item.process.stdoutConsumed),
    stderr_consumed: lifecycle.every((item) => item.process.stderrConsumed), timed_out_count: 0,
    timeout_cleanup_probe: timeout, cancellation_cleanup_probe: cancellation,
    diagnostic: lifecycle.map(diagnostic),
  } };
}
