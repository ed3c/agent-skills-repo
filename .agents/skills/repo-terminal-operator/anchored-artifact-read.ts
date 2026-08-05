import { constants, fstatSync, readFileSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";
import {
  closeWriterDirectory,
  openWriterDirectory,
  writerName,
} from "./writer-native";
import { withWriterDescriptor } from "./writer-publication-read";

const ENOENT = 2;

function failure(error: unknown): Error {
  return error instanceof Error
    ? error
    : new Error("anchored artifact read failed", { cause: error });
}

export function readAnchoredArtifact(
  root: string,
  ref: string,
  maximumBytes: number,
  label: string,
): Buffer | null {
  const output = resolve(root, ref);
  // Read intent: a shared lock. This function only ever opens O_RDONLY, so taking the
  // exclusive lock made concurrent readers of one directory serialise behind each
  // other and the loser fail with `-parent-busy` after its retry budget ran out.
  const directory = openWriterDirectory(root, dirname(output), "read");
  let primary: Error | undefined;
  let result: Buffer | null | undefined;
  try {
    if (!directory.lockAcquired) throw new Error(`${label}-parent-busy`);
    const descriptor = directory.open(
      writerName(basename(output)),
      constants.O_RDONLY | constants.O_NOFOLLOW,
    );
    if (descriptor < 0) {
      if (directory.errno() === ENOENT) result = null;
      else throw new Error(`${label}-open-failed:${directory.errno()}`);
    } else {
      result = withWriterDescriptor(descriptor, () => {
        const stat = fstatSync(descriptor);
        if (!stat.isFile() || stat.size > maximumBytes)
          throw new Error(`${label}-not-bounded-regular-file`);
        const bytes = readFileSync(descriptor);
        if (bytes.byteLength !== stat.size)
          throw new Error(`${label}-changed-during-read`);
        return bytes;
      });
    }
  } catch (error) {
    primary = failure(error);
  }
  const cleanup = closeWriterDirectory(directory).map(failure);
  if (primary || cleanup.length > 0) {
    const failures = primary ? [primary, ...cleanup] : cleanup;
    throw failures.length === 1
      ? failures[0]
      : new AggregateError(failures, `${label}-read-cleanup-failed`);
  }
  if (result === undefined) throw new Error(`${label}-read-produced-no-result`);
  return result;
}
