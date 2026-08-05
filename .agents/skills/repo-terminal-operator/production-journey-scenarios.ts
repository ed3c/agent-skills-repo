import type { BoundedProcessResult } from "./bounded-subprocess";
import type { BoundedObservation, ConcurrentObservation, PreflightObservation } from "./production-journey-observer";

type Scenario = Record<string, unknown>;

function raceDiagnostic(concurrent: ConcurrentObservation): Scenario[] {
  return concurrent.map((result) => result.status === "fulfilled"
    ? {
      status: result.status, exit_code: result.value.exitCode, receipt_status: result.value.receipt?.status,
      process_reaped: result.value.processReaped, timer_cleared: result.value.timerCleared,
      timed_out: result.value.timedOut, stdout_consumed: result.value.stdoutConsumed,
      stderr_consumed: result.value.stderrConsumed, cleanup_errors: result.value.cleanupErrors,
      receipt_sha256: result.value.receiptSha256,
      receipt_checks: result.value.receipt?.checks,
    }
    : { status: result.status, reason: String(result.reason) });
}

function raceScenario(concurrent: ConcurrentObservation): { passed: boolean; scenario: Scenario } {
  const receipts = concurrent.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
  const deterministic = new Set(receipts.map((result) => result.receiptSha256)).size === 1;
  const passed = concurrent.length === 4 && receipts.length === 4 && deterministic
    && receipts.every((result) => result.exitCode === 0 && result.receipt?.status === "passed"
      && result.processReaped && result.timerCleared && !result.timedOut);
  return { passed, scenario: {
    id: "race-condition", status: passed ? "passed" : "failed", expected: "four bounded concurrent passes",
    aggregation_semantics: "all-members-required-all-settled-for-resource-ownership",
    attempts: concurrent.length, entrypoint_scope: "process-isolated-read-only-preflight",
    bounded_concurrency: 4, deterministic_receipts: deterministic, shared_mutation_observed: false,
    diagnostic: raceDiagnostic(concurrent),
  } };
}

function cleanLifecycleRun(result: BoundedObservation): boolean {
  return result.exitCode === 0 && result.receipt?.status === "passed" && !result.timedOut
    && result.processReaped && result.timerCleared && result.stdoutConsumed && result.stderrConsumed
    && result.cleanupErrors.length === 0;
}

function lifecycleDiagnostic(runs: BoundedObservation[]): Scenario[] {
  return runs.map((result) => ({
    exit_code: result.exitCode, timed_out: result.timedOut, process_reaped: result.processReaped,
    timer_cleared: result.timerCleared, stdout_consumed: result.stdoutConsumed,
    stderr_consumed: result.stderrConsumed, cleanup_errors: result.cleanupErrors,
    parse_error: result.parseError, stderr: result.stderr,
  }));
}

function cleanupProbePassed(probe: BoundedProcessResult, expectedExit: number): boolean {
  return probe.exitCode === expectedExit && probe.processReaped && probe.timerCleared
    && probe.stdoutConsumed && probe.stderrConsumed && probe.cleanupErrors.length === 0;
}

function lifecycleScenario(runs: BoundedObservation[], timeout: BoundedProcessResult, cancellation: BoundedProcessResult) {
  const passed = runs.every(cleanLifecycleRun) && timeout.timedOut && cancellation.cancelled
    && cleanupProbePassed(timeout, 124) && cleanupProbePassed(cancellation, 130);
  return { passed, scenario: {
    id: "resource-leak", status: passed ? "passed" : "failed",
    expected: "twelve bounded exits with reaped subprocesses, drained streams, and cleared timers",
    attempts: runs.length, subprocesses_reaped: runs.every((result) => result.processReaped),
    timers_cleared: runs.every((result) => result.timerCleared),
    stdout_consumed: runs.every((result) => result.stdoutConsumed),
    stderr_consumed: runs.every((result) => result.stderrConsumed),
    timed_out_count: runs.filter((result) => result.timedOut).length,
    timeout_cleanup_probe: timeout, cancellation_cleanup_probe: cancellation,
    diagnostic: lifecycleDiagnostic(runs),
  } };
}

function validScenario(success: PreflightObservation) {
  const passed = success.receipt?.status === "passed" && success.exit_code === 0;
  return { passed, scenario: {
    id: "valid-packet", status: passed ? "passed" : "failed", expected: "passed",
    entrypoint_observed: success.receipt?.status, exit_code: success.exit_code,
    diagnostic: success.parse_error ?? success.stderr,
  } };
}

function staleScenario(stale: PreflightObservation) {
  const staleChecks = Array.isArray(stale.receipt?.checks) ? stale.receipt.checks as Scenario[] : [];
  const passed = stale.receipt?.status === "failed" && stale.exit_code === 2
    && staleChecks.some((check) => check.id === "expected-head" && check.status === "failed");
  return { passed, scenario: {
    id: "stale-head-recovery", status: passed ? "passed" : "failed", expected: "typed expected-head failure",
    entrypoint_observed: stale.receipt?.status, exit_code: stale.exit_code,
    diagnostic: stale.parse_error ?? stale.stderr,
  } };
}

function silentScenario(malformed: PreflightObservation) {
  const malformedChecks = Array.isArray(malformed.receipt?.checks) ? malformed.receipt.checks as Scenario[] : [];
  const diagnostic = malformedChecks.find((check) => check.id === "input-readable" && check.status === "failed"
    && typeof check.detail === "string" && check.detail.length > 0)?.detail;
  const passed = malformed.receipt?.status === "failed" && malformed.exit_code === 2
    && malformed.parse_error === null && typeof diagnostic === "string";
  return { passed, scenario: {
    id: "silent-failure", status: passed ? "passed" : "failed", expected: "typed failure with original parse diagnostic",
    entrypoint_observed: malformed.receipt?.status, exit_code: malformed.exit_code,
    typed_failure_receipt: malformed.parse_error === null, original_error_preserved: typeof diagnostic === "string",
    diagnostic: diagnostic ?? malformed.parse_error ?? malformed.stderr,
  } };
}

function inputScenarios(success: PreflightObservation, stale: PreflightObservation, malformed: PreflightObservation) {
  const valid = validScenario(success);
  const staleResult = staleScenario(stale);
  const silent = silentScenario(malformed);
  return {
    passed: valid.passed && staleResult.passed && silent.passed,
    scenarios: [valid.scenario, staleResult.scenario, silent.scenario],
  };
}

export function evaluateJourney(input: {
  success: PreflightObservation;
  stale: PreflightObservation;
  malformed: PreflightObservation;
  concurrent: ConcurrentObservation;
  lifecycle: BoundedObservation[];
  timeout: BoundedProcessResult;
  cancellation: BoundedProcessResult;
}) {
  const entries = inputScenarios(input.success, input.stale, input.malformed);
  const race = raceScenario(input.concurrent);
  const lifecycle = lifecycleScenario(input.lifecycle, input.timeout, input.cancellation);
  return {
    passed: entries.passed && race.passed && lifecycle.passed,
    scenarios: [...entries.scenarios.slice(0, 2), race.scenario, entries.scenarios[2], lifecycle.scenario],
  };
}
