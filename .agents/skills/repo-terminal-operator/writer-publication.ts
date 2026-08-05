import {
  constants,
  fchmodSync,
  fstatSync,
  fsyncSync,
  lstatSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { basename, dirname } from "node:path";
import {
  closeWriterDirectory,
  openWriterDirectory,
  writerName,
  type WriterDirectory,
} from "./writer-native";
import { publicationResult } from "./writer-publication-lifecycle";
import { failWriterAt, pauseWriterAt } from "./writer-publication-probe";
import {
  existingWriterOutcome,
  withWriterDescriptor,
} from "./writer-publication-read";
import type {
  RecoveryOutcome,
  WriterPublication,
} from "./writer-publication-contract";

type PublicationState = { artifactCreated: boolean };
export type WriterParentIdentity = { dev: number; ino: number };
const EEXIST = 17;
const ENOENT = 2;

function unlink(
  directory: WriterDirectory,
  name: Buffer,
  missingAllowed: boolean,
): void {
  if (directory.unlink(name) === 0) return;
  const errorNumber = directory.errno();
  if (missingAllowed && errorNumber === ENOENT) return;
  throw new Error(`[writer] failure_kind=cleanup errno=${errorNumber}`);
}

function recoverPending(
  directory: WriterDirectory,
  pending: Buffer,
  output: Buffer,
): RecoveryOutcome {
  const descriptor = directory.open(
    pending,
    constants.O_RDONLY | constants.O_NOFOLLOW,
  );
  if (descriptor < 0) {
    if (directory.errno() === ENOENT) return "none";
    throw new Error(
      `[writer] failure_kind=recovery-open errno=${directory.errno()}`,
    );
  }
  let outcome: RecoveryOutcome = "pre-link";
  outcome = withWriterDescriptor(descriptor, () => {
    const pendingFile = fstatSync(descriptor);
    if (!pendingFile.isFile() || ![1, 2].includes(pendingFile.nlink)) {
      throw new Error("[writer] failure_kind=unsafe-recovery-pending");
    }
    if (pendingFile.nlink === 2) {
      outcome = "post-link";
      const outputDescriptor = directory.open(
        output,
        constants.O_RDONLY | constants.O_NOFOLLOW,
      );
      if (outputDescriptor < 0)
        throw new Error(
          `[writer] failure_kind=recovery-output errno=${directory.errno()}`,
        );
      withWriterDescriptor(outputDescriptor, () => {
        const outputFile = fstatSync(outputDescriptor);
        if (
          outputFile.dev !== pendingFile.dev ||
          outputFile.ino !== pendingFile.ino
        ) {
          throw new Error("[writer] failure_kind=recovery-link-mismatch");
        }
      });
    }
    return outcome;
  });
  unlink(directory, pending, false);
  return outcome;
}

function writePending(
  directory: WriterDirectory,
  pending: Buffer,
  candidate: Buffer,
): void {
  const descriptor = directory.open(
    pending,
    constants.O_WRONLY |
      constants.O_CREAT |
      constants.O_EXCL |
      constants.O_NOFOLLOW,
  );
  if (descriptor < 0)
    throw new Error(
      `[writer] failure_kind=temp-open errno=${directory.errno()}`,
    );
  withWriterDescriptor(descriptor, () => {
    const file = fstatSync(descriptor);
    if (!file.isFile() || file.nlink !== 1)
      throw new Error("[writer] failure_kind=unsafe-pending");
    fchmodSync(descriptor, 0o600);
    writeFileSync(descriptor, candidate);
    fsyncSync(descriptor);
  });
}

function assertStable(directory: WriterDirectory): void {
  const rootAfter = lstatSync(directory.rootPath);
  const parentAfter = lstatSync(directory.parentPath);
  if (
    rootAfter.dev !== directory.rootBefore.dev ||
    rootAfter.ino !== directory.rootBefore.ino ||
    parentAfter.dev !== directory.parentBefore.dev ||
    parentAfter.ino !== directory.parentBefore.ino
  ) {
    throw new Error(
      "[writer] failure_kind=boundary-drift root or parent changed",
    );
  }
}

function verifyPublished(
  directory: WriterDirectory,
  output: Buffer,
  candidate: Buffer,
): void {
  const descriptor = directory.open(
    output,
    constants.O_RDONLY | constants.O_NOFOLLOW,
  );
  if (descriptor < 0)
    throw new Error(`[writer] failure_kind=reopen errno=${directory.errno()}`);
  withWriterDescriptor(descriptor, () => {
    const file = fstatSync(descriptor);
    if (!file.isFile() || file.nlink !== 2)
      throw new Error("[writer] failure_kind=unsafe-published-link");
    if (!readFileSync(descriptor).equals(candidate))
      throw new Error("[writer] failure_kind=publication-mismatch");
  });
}

function publishLocked(
  directory: WriterDirectory,
  output: Buffer,
  pending: Buffer,
  candidate: Buffer,
  state: PublicationState,
): WriterPublication {
  const recoveryOutcome = recoverPending(directory, pending, output);
  const existing = existingWriterOutcome(directory, output, candidate);
  if (existing) return { writerOutcome: existing, recoveryOutcome };
  writePending(directory, pending, candidate);
  pauseWriterAt("pending-written");
  if (directory.link(pending, output) !== 0) {
    const errorNumber = directory.errno();
    if (errorNumber === EEXIST) {
      const outcome = existingWriterOutcome(directory, output, candidate);
      if (outcome) return { writerOutcome: outcome, recoveryOutcome };
      throw new Error("[writer] output disappeared after conflict");
    }
    throw new Error(`[writer] failure_kind=link errno=${errorNumber}`);
  }
  state.artifactCreated = true;
  pauseWriterAt("output-linked");
  failWriterAt("output-linked");
  fsyncSync(directory.descriptor);
  assertStable(directory);
  verifyPublished(directory, output, candidate);
  return { writerOutcome: "published", recoveryOutcome };
}

function publishWithOwnership(
  directory: WriterDirectory,
  output: Buffer,
  pending: Buffer,
  candidate: Buffer,
  state: PublicationState,
): WriterPublication {
  if (directory.lockAcquired)
    return publishLocked(directory, output, pending, candidate, state);
  const existing = existingWriterOutcome(directory, output, candidate);
  if (existing) return { writerOutcome: existing, recoveryOutcome: "none" };
  throw new Error(
    "[writer] failure_kind=busy retry_budget=3 wait_budget_ms=600",
  );
}

export function publishWriterArtifact(
  root: string,
  outputPath: string,
  candidate: Buffer,
  expectedParent?: WriterParentIdentity,
): WriterPublication {
  const parent = dirname(outputPath);
  const output = writerName(basename(outputPath));
  const pending = writerName(`.${basename(outputPath)}.writer-pending`);
  const directory = openWriterDirectory(root, parent);
  if (
    expectedParent !== undefined &&
    (directory.parentBefore.dev !== expectedParent.dev ||
      directory.parentBefore.ino !== expectedParent.ino)
  ) {
    const cleanup = closeWriterDirectory(directory);
    throw new AggregateError(
      [new Error("[writer] failure_kind=parent-identity-mismatch"), ...cleanup],
      "writer parent identity changed before publication",
    );
  }
  const state: PublicationState = { artifactCreated: false };
  let primary: unknown;
  let publication: WriterPublication | undefined;
  try {
    publication = publishWithOwnership(
      directory,
      output,
      pending,
      candidate,
      state,
    );
  } catch (error) {
    primary = error;
  }
  const cleanupFailures: unknown[] = [];
  if (directory.lockAcquired) {
    try {
      unlink(directory, pending, true);
    } catch (error) {
      cleanupFailures.push(error);
    }
  }
  cleanupFailures.push(...closeWriterDirectory(directory));
  return publicationResult(
    publication,
    primary,
    cleanupFailures,
    state.artifactCreated,
  );
}

export function removeWriterArtifact(
  root: string,
  outputPath: string,
  expected: Buffer,
  expectedParent?: WriterParentIdentity,
): void {
  const directory = openWriterDirectory(root, dirname(outputPath));
  const output = writerName(basename(outputPath));
  let primary: unknown;
  try {
    if (
      expectedParent !== undefined &&
      (directory.parentBefore.dev !== expectedParent.dev ||
        directory.parentBefore.ino !== expectedParent.ino)
    )
      throw new Error("[writer] failure_kind=parent-identity-mismatch");
    if (!directory.lockAcquired)
      throw new Error(
        "[writer] failure_kind=busy retry_budget=3 wait_budget_ms=600",
      );
    const descriptor = directory.open(
      output,
      constants.O_RDONLY | constants.O_NOFOLLOW,
    );
    if (descriptor < 0) {
      if (directory.errno() !== ENOENT)
        throw new Error(
          `[writer] failure_kind=remove-open errno=${directory.errno()}`,
        );
    } else {
      withWriterDescriptor(descriptor, () => {
        const file = fstatSync(descriptor);
        if (!file.isFile() || file.nlink < 1)
          throw new Error("[writer] failure_kind=unsafe-remove-target");
        if (!readFileSync(descriptor).equals(expected))
          throw new Error("[writer] failure_kind=remove-content-mismatch");
      });
      unlink(directory, output, false);
      fsyncSync(directory.descriptor);
      assertStable(directory);
    }
  } catch (error) {
    primary = error;
  }
  const cleanupFailures = closeWriterDirectory(directory);
  if (primary !== undefined && cleanupFailures.length > 0)
    throw new AggregateError(
      [primary, ...cleanupFailures],
      "writer removal and cleanup failed",
      { cause: primary },
    );
  if (primary !== undefined) throw primary;
  if (cleanupFailures.length > 0)
    throw new AggregateError(cleanupFailures, "writer removal cleanup failed");
}
