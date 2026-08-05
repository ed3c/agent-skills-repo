import type { WriterJourneyPaths } from "./writer-production-fixture";
import type { StageKillObservation, WriterObservation } from "./writer-production-observer";
import { cleanProcess, diagnostic } from "./writer-production-scenarios";

type RecordValue = Record<string, unknown>;
type SettledWriter = PromiseSettledResult<WriterObservation>;
type WriterRaceInput = {
  concurrent: SettledWriter[];
  conflict: WriterObservation;
  rollbackKill: StageKillObservation;
  rollbackRecovery: WriterObservation;
  rollbackResidue: number;
  rollbackPreserved: boolean;
  cancellationKill: StageKillObservation;
  cancellationRecovery: WriterObservation;
  cancellationResidue: number;
  cancellationAbsentBeforeRecovery: boolean;
  paths: WriterJourneyPaths;
};
type ConcurrentEvidence = { passed: boolean; runs: RecordValue[]; outcomes: unknown[]; outputHashes: unknown[] };

function probeIdentity(observation: WriterObservation): RecordValue {
  return {
    artifact_id: observation.receipt?.artifact_id, run_id: observation.receipt?.run_id,
    exit_code: observation.process.exitCode, failure_kind: observation.receipt?.failure_kind,
    typed_failure_receipt: observation.receipt?.typed_failure_receipt,
    original_error_preserved: observation.receipt?.original_error_preserved,
    artifact_created: observation.receipt?.artifact_created,
  };
}

function cleanStage(stage: StageKillObservation): boolean {
  return stage.stageReady && stage.killed && stage.processReaped && stage.timerCleared
    && stage.stdoutConsumed && stage.stderrConsumed && !stage.timedOut;
}

function concurrentEvidence(input: WriterRaceInput): ConcurrentEvidence {
  const observations = input.concurrent.flatMap((item) => item.status === "fulfilled" ? [item.value] : []);
  const runs = observations.map(diagnostic);
  const outcomes = runs.map((run) => run.writer_outcome);
  const outputHashes = runs.map((run) => run.output_sha256);
  const passed = observations.length === 4 && observations.every((item) => item.process.exitCode === 0 && cleanProcess(item.process))
    && outcomes.filter((value) => value === "published").length === 1
    && outcomes.filter((value) => value === "matched-existing").length === 3
    && new Set(outputHashes).size === 1 && outputHashes[0] === input.paths.outputSha256;
  return { passed, runs, outcomes, outputHashes };
}

function rollbackPassed(input: WriterRaceInput): boolean {
  return cleanStage(input.rollbackKill) && input.rollbackRecovery.process.exitCode === 0
    && input.rollbackRecovery.receipt?.writer_outcome === "matched-existing"
    && input.rollbackRecovery.receipt?.recovery_outcome === "post-link"
    && input.rollbackResidue === 0 && input.rollbackPreserved;
}

function cancellationPassed(input: WriterRaceInput): boolean {
  return cleanStage(input.cancellationKill) && input.cancellationKill.exitCode !== 0
    && input.cancellationRecovery.process.exitCode === 0
    && input.cancellationRecovery.receipt?.recovery_outcome === "pre-link"
    && input.cancellationResidue === 0 && input.cancellationAbsentBeforeRecovery;
}

function rollbackProbe(input: WriterRaceInput): RecordValue {
  const recovery = input.rollbackRecovery.receipt;
  return {
    artifact_id: recovery?.artifact_id, run_id: recovery?.run_id, injected_stage: "post-link",
    killed: input.rollbackKill.killed, process_reaped: input.rollbackKill.processReaped,
    stdout_consumed: input.rollbackKill.stdoutConsumed, stderr_consumed: input.rollbackKill.stderrConsumed,
    recovered: input.rollbackRecovery.receipt?.recovery_outcome === "post-link",
    residue_count: input.rollbackResidue, final_output_preserved: input.rollbackPreserved,
    output_sha256: input.rollbackRecovery.receipt?.output_sha256,
  };
}

function cancellationProbe(input: WriterRaceInput): RecordValue {
  const recovery = input.cancellationRecovery.receipt;
  return {
    artifact_id: recovery?.artifact_id, run_id: recovery?.run_id, injected_stage: "pending-written",
    exit_code: input.cancellationKill.exitCode,
    cancelled: input.cancellationKill.killed, killed: input.cancellationKill.killed,
    process_reaped: input.cancellationKill.processReaped, timer_cleared: input.cancellationKill.timerCleared,
    stdout_consumed: input.cancellationKill.stdoutConsumed, stderr_consumed: input.cancellationKill.stderrConsumed,
    recovered: input.cancellationRecovery.receipt?.recovery_outcome === "pre-link",
    final_output_absent_before_recovery: input.cancellationAbsentBeforeRecovery,
    residue_count: input.cancellationResidue,
  };
}

export function writerRaceScenario(input: WriterRaceInput) {
  const concurrent = concurrentEvidence(input);
  const conflictPassed = input.conflict.process.exitCode === 1 && cleanProcess(input.conflict.process)
    && input.conflict.receipt?.failure_kind === "conflict";
  const passed = concurrent.passed && conflictPassed && rollbackPassed(input) && cancellationPassed(input);
  return { passed, scenario: {
    id: "race-condition", status: passed ? "passed" : "failed", attempts: 4, bounded_concurrency: 4,
    entrypoint_scope: "process-isolated-immutable-writer", shared_mutation_observed: true,
    deterministic_outputs: new Set(concurrent.outputHashes).size === 1, deterministic_receipts: false,
    published_count: concurrent.outcomes.filter((value) => value === "published").length,
    matched_existing_count: concurrent.outcomes.filter((value) => value === "matched-existing").length,
    diagnostic: concurrent.runs, conflict_probe: probeIdentity(input.conflict),
    rollback_probe: rollbackProbe(input), cancellation_probe: cancellationProbe(input),
  } };
}
