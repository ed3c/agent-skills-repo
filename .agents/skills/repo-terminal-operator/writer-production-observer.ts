import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { runBoundedProcess, type BoundedProcessResult } from "./bounded-subprocess";
import type { WriterJourneyPaths } from "./writer-production-fixture";

type RecordValue = Record<string, unknown>;
export type WriterObservation = { process: BoundedProcessResult; receipt: RecordValue };
export type StageKillObservation = {
  stageReady: boolean;
  killed: boolean;
  processReaped: boolean;
  timerCleared: boolean;
  stdoutConsumed: boolean;
  stderrConsumed: boolean;
  timedOut: boolean;
  exitCode: number;
  stdout: string;
  stderr: string;
};
type StageLifecycle = {
  processReaped: boolean;
  timerCleared: boolean;
  stdoutConsumed: boolean;
  stderrConsumed: boolean;
  readerReleased: boolean;
};

function parseLastJson(value: string): RecordValue {
  const line = value.trim().split(/\r?\n/u).filter(Boolean).at(-1);
  if (!line) throw new Error("writer emitted no JSON receipt");
  try { return JSON.parse(line) as RecordValue; }
  catch (error) { throw new Error("writer emitted invalid JSON", { cause: error }); }
}

function writerArgv(entrypoint: string, paths: WriterJourneyPaths, candidate: string, output: string, runId: string): string[] {
  return [
    "bun", "run", entrypoint,
    "--root", paths.root, "--output", output, "--candidate", candidate,
    "--artifact-id", "writer-output-v1", "--run-id", runId,
  ];
}

export async function observeWriter(
  entrypoint: string,
  cwd: string,
  paths: WriterJourneyPaths,
  candidate: string,
  output: string,
  runId: string,
): Promise<WriterObservation> {
  const process = await runBoundedProcess(writerArgv(entrypoint, paths, candidate, output, runId), { cwd, timeoutMs: 5_000 });
  return { process, receipt: parseLastJson(process.exitCode === 0 ? process.stdout : process.stderr) };
}

export function observeConcurrentWriters(entrypoint: string, cwd: string, paths: WriterJourneyPaths) {
  return Promise.allSettled(
    ["writer-race-1", "writer-race-2", "writer-race-3", "writer-race-4"]
      .map((runId) => observeWriter(entrypoint, cwd, paths, paths.candidate, paths.raceOutput, runId)),
  );
}

async function consumeReader(reader: ReadableStreamDefaultReader<Uint8Array>, diagnostic: string): Promise<string> {
  let output = diagnostic;
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) return output;
    output += new TextDecoder().decode(chunk.value, { stream: true });
  }
}

async function waitForStage(
  child: Bun.Subprocess<"ignore", "pipe", "pipe">,
  reader: ReadableStreamDefaultReader<Uint8Array>,
  stage: "pending-written" | "output-linked",
): Promise<{ stderr: string; stageReady: boolean }> {
  let stderr = "";
  while (!stderr.includes(`"stage":"${stage}"`)) {
    const chunk = await reader.read();
    if (chunk.done) return { stderr, stageReady: false };
    stderr += new TextDecoder().decode(chunk.value, { stream: true });
  }
  child.kill(9);
  return { stderr, stageReady: true };
}

function spawnStageWriter(
  entrypoint: string,
  cwd: string,
  paths: WriterJourneyPaths,
  output: string,
  runId: string,
  stage: "pending-written" | "output-linked",
) {
  return Bun.spawn(writerArgv(entrypoint, paths, paths.candidate, output, runId), {
    cwd, stdout: "pipe", stderr: "pipe",
    env: { ...process.env, SKILL_BETTOR_WRITER_PROBE_STAGE: stage },
  });
}

async function cleanupStageObservation(
  child: Bun.Subprocess<"ignore", "pipe", "pipe">,
  reader: ReadableStreamDefaultReader<Uint8Array>,
  stdout: Promise<string>,
  timer: ReturnType<typeof setTimeout>,
  lifecycle: StageLifecycle,
): Promise<unknown[]> {
  const errors: unknown[] = [];
  if (!lifecycle.timerCleared) { clearTimeout(timer); lifecycle.timerCleared = true; }
  if (!lifecycle.processReaped) {
    try { child.kill(9); await child.exited; lifecycle.processReaped = true; } catch (error) { errors.push(error); }
  }
  if (!lifecycle.stderrConsumed) {
    try { await consumeReader(reader, ""); lifecycle.stderrConsumed = true; } catch (error) { errors.push(error); }
  }
  if (!lifecycle.readerReleased) {
    try { reader.releaseLock(); lifecycle.readerReleased = true; } catch (error) { errors.push(error); }
  }
  if (!lifecycle.stdoutConsumed) {
    try { await stdout; lifecycle.stdoutConsumed = true; } catch (error) { errors.push(error); }
  }
  return errors;
}

async function collectStageObservation(
  child: Bun.Subprocess<"ignore", "pipe", "pipe">,
  reader: ReadableStreamDefaultReader<Uint8Array>,
  stdoutPromise: Promise<string>,
  timer: ReturnType<typeof setTimeout>,
  lifecycle: StageLifecycle,
  stage: "pending-written" | "output-linked",
  timedOut: () => boolean,
): Promise<StageKillObservation> {
  const observed = await waitForStage(child, reader, stage);
  const exitCode = await child.exited;
  lifecycle.processReaped = true;
  clearTimeout(timer);
  lifecycle.timerCleared = true;
  const stderr = await consumeReader(reader, observed.stderr);
  lifecycle.stderrConsumed = true;
  reader.releaseLock();
  lifecycle.readerReleased = true;
  const stdout = await stdoutPromise;
  lifecycle.stdoutConsumed = true;
  return {
    stageReady: observed.stageReady, killed: observed.stageReady,
    processReaped: lifecycle.processReaped, timerCleared: lifecycle.timerCleared,
    stdoutConsumed: lifecycle.stdoutConsumed, stderrConsumed: lifecycle.stderrConsumed,
    timedOut: timedOut(), exitCode, stdout, stderr,
  };
}

export async function observeStageKill(
  entrypoint: string,
  cwd: string,
  paths: WriterJourneyPaths,
  output: string,
  runId: string,
  stage: "pending-written" | "output-linked",
): Promise<StageKillObservation> {
  const child = spawnStageWriter(entrypoint, cwd, paths, output, runId, stage);
  const stdoutPromise = new Response(child.stdout).text();
  const reader = child.stderr.getReader();
  let timedOut = false;
  const lifecycle: StageLifecycle = {
    processReaped: false, timerCleared: false, stdoutConsumed: false,
    stderrConsumed: false, readerReleased: false,
  };
  let primary: unknown;
  const timer = setTimeout(() => { timedOut = true; child.kill(9); }, 1_000);
  try {
    return await collectStageObservation(child, reader, stdoutPromise, timer, lifecycle, stage, () => timedOut);
  } catch (error) {
    primary = error;
    throw error;
  } finally {
    const cleanupErrors = await cleanupStageObservation(child, reader, stdoutPromise, timer, lifecycle);
    if (cleanupErrors.length > 0) {
      throw new AggregateError(primary === undefined ? cleanupErrors : [primary, ...cleanupErrors], "stage-kill observation cleanup failed");
    }
  }
}

export function writerResidue(paths: WriterJourneyPaths, output: string): number {
  const parent = join(paths.root, output.split("/")[0] ?? "");
  return readdirSync(parent).filter((name) => name.includes("writer-pending")).length;
}

export function publishedOutput(paths: WriterJourneyPaths, output: string): Buffer {
  return readFileSync(join(paths.root, output));
}
