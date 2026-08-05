import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  openSync,
  type Stats,
} from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import {
  loadWriterNativeLibrary,
  type WriterNativeLibrary,
} from "./writer-native-library";

export type WriterDirectory = {
  descriptor: number;
  descriptors: number[];
  lockAcquired: boolean;
  rootPath: string;
  rootBefore: Stats;
  parentPath: string;
  parentBefore: Stats;
  library: WriterNativeLibrary;
  open(name: Buffer, flags: number): number;
  link(source: Buffer, target: Buffer): number;
  unlink(name: Buffer): number;
  errno(): number;
};

export function writerName(value: string): Buffer {
  if (!value || value.includes("/") || value.includes("\0"))
    throw new Error(`unsafe writer name: ${JSON.stringify(value)}`);
  return Buffer.from(`${value}\0`);
}

function relativeParts(root: string, parent: string): string[] {
  const local = relative(root, parent);
  if (isAbsolute(local) || local === ".." || local.startsWith(`..${sep}`))
    throw new Error("writer parent escapes root");
  return local === "" ? [] : local.split(sep);
}

function discard(
  library: WriterNativeLibrary,
  descriptors: number[],
  primary: unknown,
): never {
  const failures: unknown[] = [primary];
  try {
    library.close();
  } catch (error) {
    failures.push(error);
  }
  for (const descriptor of descriptors.reverse()) {
    try {
      closeSync(descriptor);
    } catch (error) {
      failures.push(error);
    }
  }
  if (failures.length > 1)
    throw new AggregateError(
      failures,
      "writer directory open and cleanup failed",
    );
  throw primary;
}

function closeAfterLoadFailure(descriptor: number, primary: unknown): never {
  try {
    closeSync(descriptor);
  } catch (cleanup) {
    throw new AggregateError(
      [primary, cleanup],
      "native library load and descriptor cleanup failed",
    );
  }
  throw primary;
}

function walk(
  library: WriterNativeLibrary,
  root: number,
  parts: string[],
  descriptors: number[],
): number {
  let current = root;
  for (const part of parts) {
    const next = library.openat(
      current,
      writerName(part),
      constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW,
      0,
    );
    if (next < 0)
      throw new Error(
        `writer directory component refused: ${part}; errno=${library.errno()}`,
      );
    descriptors.push(next);
    current = next;
  }
  return current;
}

// LOCK_SH=1, LOCK_EX=2, LOCK_NB=4. Readers take the shared mode so that concurrent
// readers of the same directory do not exclude one another: an exclusive lock on a
// read path made four identical collectors serialise, and whichever one exhausted the
// retry budget failed with `-parent-busy` rather than converging. Writers keep the
// exclusive mode, so writer-versus-writer and writer-versus-reader exclusion is
// unchanged -- only reader-versus-reader is allowed to proceed together.
const LOCK_SHARED = 1 | 4;
const LOCK_EXCLUSIVE = 2 | 4;

function acquireWriterLock(
  library: WriterNativeLibrary,
  descriptor: number,
  mode: number,
): boolean {
  const wouldBlock = process.platform === "darwin" ? 35 : 11;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    if (library.flock(descriptor, mode) === 0) return true;
    const errorNumber = library.errno();
    if (errorNumber !== wouldBlock)
      throw new Error(`[writer] failure_kind=lock errno=${errorNumber}`);
    if (attempt === 3) return false;
    Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, attempt * 200);
  }
  return false;
}

function assertRootOpened(rootBefore: Stats, descriptor: number): void {
  const opened = fstatSync(descriptor);
  if (opened.dev !== rootBefore.dev || opened.ino !== rootBefore.ino)
    throw new Error("writer root changed before open");
}

function parentIdentity(parentPath: string, descriptor: number): Stats {
  const before = lstatSync(parentPath);
  const opened = fstatSync(descriptor);
  if (
    !before.isDirectory() ||
    before.isSymbolicLink() ||
    opened.dev !== before.dev ||
    opened.ino !== before.ino
  ) {
    throw new Error("writer parent changed before anchored open");
  }
  return before;
}

export function openWriterDirectory(
  root: string,
  parent: string,
  // Defaults to exclusive: a caller that does not say it is only reading gets the
  // stricter lock, so adding a new writer cannot weaken exclusion by omission.
  intent: "read" | "write" = "write",
): WriterDirectory {
  const rootPath = resolve(root);
  const parentPath = resolve(parent);
  const rootBefore = lstatSync(rootPath);
  if (!rootBefore.isDirectory() || rootBefore.isSymbolicLink())
    throw new Error("writer root must be a real directory");
  const rootDescriptor = openSync(
    rootPath,
    constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW,
  );
  const descriptors = [rootDescriptor];
  let library: WriterNativeLibrary;
  try {
    library = loadWriterNativeLibrary();
  } catch (error) {
    closeAfterLoadFailure(rootDescriptor, error);
  }
  try {
    assertRootOpened(rootBefore, rootDescriptor);
    const descriptor = walk(
      library,
      rootDescriptor,
      relativeParts(rootPath, parentPath),
      descriptors,
    );
    const parentBefore = parentIdentity(parentPath, descriptor);
    const lockAcquired = acquireWriterLock(
      library,
      descriptor,
      intent === "read" ? LOCK_SHARED : LOCK_EXCLUSIVE,
    );
    return {
      descriptor,
      descriptors,
      lockAcquired,
      rootPath,
      rootBefore,
      parentPath,
      parentBefore,
      library,
      open: (name, flags) => library.openat(descriptor, name, flags, 0o600),
      link: (source, target) =>
        library.linkat(descriptor, source, descriptor, target, 0),
      unlink: (name) => library.unlinkat(descriptor, name, 0),
      errno: () => library.errno(),
    };
  } catch (error) {
    discard(library, descriptors, error);
  }
}

export function closeWriterDirectory(directory: WriterDirectory): unknown[] {
  const failures: unknown[] = [];
  if (
    directory.lockAcquired &&
    directory.library.flock(directory.descriptor, 8) !== 0
  ) {
    failures.push(
      new Error(`writer unlock failed: errno=${directory.errno()}`),
    );
  }
  try {
    directory.library.close();
  } catch (error) {
    failures.push(error);
  }
  for (const descriptor of [...directory.descriptors].reverse()) {
    try {
      closeSync(descriptor);
    } catch (error) {
      failures.push(error);
    }
  }
  return failures;
}
