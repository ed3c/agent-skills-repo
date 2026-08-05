import { createHash } from "node:crypto";
import { lstatSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import type { AsyncWorkerProgress } from "./async-worker-carrier";
import { publishWriterArtifact } from "./writer-publication";

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function realDirectory(root: string, label: string): string {
  try {
    mkdirSync(root, { mode: 0o700 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  }
  const stat = lstatSync(root);
  if (!stat.isDirectory() || stat.isSymbolicLink())
    throw new Error(`${label}-must-be-real-directory`);
  return root;
}

export function persistAsyncWorkerProgress(
  stateRoot: string,
  event: AsyncWorkerProgress,
): { ref: string; sha256: string } {
  const root = realDirectory(join(stateRoot, "progress"), "progress-root");
  const runRoot = realDirectory(join(root, event.run_id), "progress-run-root");
  const name = `${event.fencing_token}.${String(event.sequence).padStart(2, "0")}.json`;
  const ref = `progress/${event.run_id}/${name}`;
  const output = join(runRoot, name);
  const bytes = Buffer.from(`${JSON.stringify(event)}\n`);
  publishWriterArtifact(stateRoot, output, bytes);
  const reopened = readFileSync(output);
  if (!reopened.equals(bytes)) throw new Error("progress-readback-mismatch");
  return { ref, sha256: sha256(reopened) };
}
