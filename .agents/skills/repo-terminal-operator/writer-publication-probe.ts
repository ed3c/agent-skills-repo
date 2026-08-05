export function pauseWriterAt(stage: "pending-written" | "output-linked"): void {
  if (process.env.SKILL_BETTOR_WRITER_PROBE_STAGE !== stage) return;
  console.error(JSON.stringify({ schema_version: "repo-terminal-writer-stage@v1", stage, pid: process.pid }));
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 5_000);
}

export function failWriterAt(stage: "output-linked"): void {
  if (process.env.SKILL_BETTOR_WRITER_FAIL_STAGE === stage) {
    throw new Error(`[writer] failure_kind=injected stage=${stage}`);
  }
}
