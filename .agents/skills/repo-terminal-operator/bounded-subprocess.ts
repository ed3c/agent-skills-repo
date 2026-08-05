export type BoundedProcessOptions = {
  cwd: string;
  timeoutMs: number;
  signal?: AbortSignal;
};

export type BoundedProcessResult = {
  exitCode: number;
  stdout: string;
  stderr: string;
  timedOut: boolean;
  cancelled: boolean;
  processReaped: boolean;
  timerCleared: boolean;
  stdoutConsumed: boolean;
  stderrConsumed: boolean;
  cleanupErrors: string[];
};

export async function runBoundedProcess(command: string[], options: BoundedProcessOptions): Promise<BoundedProcessResult> {
  if (command.length === 0) throw new Error("bounded process requires a non-empty argv");
  if (!Number.isFinite(options.timeoutMs) || options.timeoutMs <= 0) throw new Error("bounded process requires a positive timeout");

  const child = Bun.spawn(command, { cwd: options.cwd, stdout: "pipe", stderr: "pipe" });
  const stdoutPromise = new Response(child.stdout).text();
  const stderrPromise = new Response(child.stderr).text();
  let timedOut = false;
  let cancelled = false;
  let processReaped = false;
  let timerCleared = false;
  let stdoutConsumed = false;
  let stderrConsumed = false;
  let stdout = "";
  let stderr = "";
  let exitCode = 1;
  const cleanupErrors: string[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abortHandler: (() => void) | undefined;

  const observedExit = child.exited.then((code) => {
    processReaped = true;
    return code;
  });
  const timeoutExit = new Promise<number>((resolveExit) => {
    timer = setTimeout(() => {
      timedOut = true;
      child.kill(9);
      resolveExit(124);
    }, options.timeoutMs);
  });
  const cancellationExit = new Promise<number>((resolveExit) => {
    abortHandler = () => {
      cancelled = true;
      child.kill(9);
      resolveExit(130);
    };
    if (options.signal?.aborted) abortHandler();
    else options.signal?.addEventListener("abort", abortHandler, { once: true });
  });

  try {
    exitCode = await Promise.race([observedExit, timeoutExit, cancellationExit]);
  } finally {
    if (timer !== undefined) {
      clearTimeout(timer);
      timerCleared = true;
    }
    if (abortHandler) options.signal?.removeEventListener("abort", abortHandler);
    if (!processReaped) child.kill(9);
    try {
      await observedExit;
      processReaped = true;
    } catch (error) {
      cleanupErrors.push(`process-reap: ${error instanceof Error ? error.message : String(error)}`);
    }
    const [stdoutResult, stderrResult] = await Promise.allSettled([stdoutPromise, stderrPromise]);
    if (stdoutResult.status === "fulfilled") {
      stdout = stdoutResult.value;
      stdoutConsumed = true;
    } else cleanupErrors.push(`stdout-drain: ${String(stdoutResult.reason)}`);
    if (stderrResult.status === "fulfilled") {
      stderr = stderrResult.value;
      stderrConsumed = true;
    } else cleanupErrors.push(`stderr-drain: ${String(stderrResult.reason)}`);
  }

  return { exitCode, stdout, stderr, timedOut, cancelled, processReaped, timerCleared, stdoutConsumed, stderrConsumed, cleanupErrors };
}
