import { createHash } from "node:crypto";
import { closeSync, constants, fstatSync, readFileSync } from "node:fs";
import type { WriterDirectory } from "./writer-native";
import type { WriterOutcome } from "./writer-publication-contract";

const ENOENT = 2;

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

export function withWriterDescriptor<T>(descriptor: number, action: () => T): T {
  let primary: unknown;
  try { return action(); }
  catch (error) { primary = error; throw error; }
  finally {
    try { closeSync(descriptor); }
    catch (error) {
      throw new AggregateError(primary === undefined ? [error] : [primary, error], "writer descriptor cleanup failed");
    }
  }
}

function readAnchored(directory: WriterDirectory, name: Buffer): Buffer | undefined {
  const descriptor = directory.open(name, constants.O_RDONLY | constants.O_NOFOLLOW);
  if (descriptor < 0) {
    if (directory.errno() === ENOENT) return undefined;
    throw new Error(`[writer] failure_kind=read errno=${directory.errno()}`);
  }
  return withWriterDescriptor(descriptor, () => {
    const file = fstatSync(descriptor);
    if (!file.isFile()) throw new Error("[writer] failure_kind=unsafe-output non-regular file");
    return readFileSync(descriptor);
  });
}

export function existingWriterOutcome(
  directory: WriterDirectory,
  output: Buffer,
  candidate: Buffer,
): WriterOutcome | undefined {
  const existing = readAnchored(directory, output);
  if (existing === undefined) return undefined;
  if (existing.equals(candidate)) return "matched-existing";
  throw new Error(
    `[writer] failure_kind=conflict existing_sha256=${sha256(existing)} candidate_sha256=${sha256(candidate)}`,
  );
}
