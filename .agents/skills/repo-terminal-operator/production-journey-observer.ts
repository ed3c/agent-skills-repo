import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { runBoundedProcess } from "./bounded-subprocess";

export type PreflightObservation = ReturnType<typeof runPreflight>;
export type BoundedObservation = Awaited<ReturnType<typeof runBoundedPreflight>>;
export type ConcurrentObservation = PromiseSettledResult<BoundedObservation>[];

export function runPreflight(adapter: string, outputRepo: string, packetPath: string) {
  const result = spawnSync("bun", ["run", adapter, "--preflight", packetPath], { cwd: outputRepo, encoding: "utf8", timeout: 5_000 });
  let receipt: Record<string, unknown> | null = null;
  let parseError: string | null = null;
  try {
    receipt = JSON.parse(result.stdout) as Record<string, unknown>;
  } catch (error) {
    parseError = error instanceof Error ? error.message : String(error);
  }
  return {
    exit_code: result.status, receipt, stderr: result.stderr.trim(), parse_error: parseError,
    timed_out: result.error ? Reflect.get(result.error, "code") === "ETIMEDOUT" : false,
  };
}

export async function runBoundedPreflight(adapter: string, outputRepo: string, packetPath: string) {
  const result = await runBoundedProcess(["bun", "run", adapter, "--preflight", packetPath], { cwd: outputRepo, timeoutMs: 5_000 });
  let receipt: Record<string, unknown> | null = null;
  let parseError: string | null = null;
  try {
    receipt = JSON.parse(result.stdout) as Record<string, unknown>;
  } catch (error) {
    parseError = error instanceof Error ? error.message : String(error);
  }
  return {
    ...result, receipt, parseError, stderr: result.stderr.trim(),
    receiptSha256: createHash("sha256").update(result.stdout.trim()).digest("hex"),
  };
}

export function runConcurrent(adapter: string, outputRepo: string, packetPath: string): Promise<ConcurrentObservation> {
  return Promise.allSettled(Array.from({ length: 4 }, () => runBoundedPreflight(adapter, outputRepo, packetPath)));
}

export async function runLifecycle(adapter: string, outputRepo: string, packetPath: string): Promise<BoundedObservation[]> {
  const runs: BoundedObservation[] = [];
  for (let index = 0; index < 12; index += 1) runs.push(await runBoundedPreflight(adapter, outputRepo, packetPath));
  return runs;
}

export async function runCleanupProbes(outputRepo: string) {
  const timeout = await runBoundedProcess(["bun", "-e", "setInterval(() => {}, 1000)"], { cwd: outputRepo, timeoutMs: 20 });
  const controller = new AbortController();
  const cancellationPending = runBoundedProcess(
    ["bun", "-e", "setInterval(() => {}, 1000)"],
    { cwd: outputRepo, timeoutMs: 5_000, signal: controller.signal },
  );
  controller.abort();
  return { timeout, cancellation: await cancellationPending };
}
