#!/usr/bin/env bun
import { createHash } from "node:crypto";
import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  openSync,
  readFileSync,
  readdirSync,
} from "node:fs";
import { isAbsolute, join, normalize, relative, resolve, sep } from "node:path";
import { publishWriterArtifact } from "./writer-publication";

const RUN_ID = /^[a-z0-9][a-z0-9._-]{0,127}$/u;
const SHA256 = /^[a-f0-9]{64}$/u;
const GIT_OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/u;
const MAX_RECORD_BYTES = 16 * 1024 * 1024;

export type AsyncSealInput = {
  runId: string;
  jobId: string;
  targetRepo: string;
  expectedHead: string;
  deadlineAt: string;
  candidateFiles: Array<{ path: string; bytes: Uint8Array }>;
  foregroundReceipt: Uint8Array;
  productionJob: Uint8Array;
};

export type AsyncClaimInput = {
  runId: string;
  expectedVersion: number;
  expectedSealSha256: string;
  workerId: string;
  now: Date;
  leaseExpiresAt: Date;
  fencingToken: string;
};

export type AsyncFinishInput = {
  runId: string;
  expectedVersion: number;
  fencingToken: string;
  now: Date;
  terminalStatus: "verified" | "failed" | "stale";
  resultRef: string;
  resultSha256: string;
  transactionParentDev?: number;
  transactionParentIno?: number;
};

export type AsyncCancelInput = {
  runId: string;
  expectedVersion: number;
  reason: string;
  now: Date;
};

type StoredCandidate = { path: string; sha256: string; contentBase64: string };
type SealRecord = {
  schemaVersion: "repo-async-production-seal@v2";
  runId: string;
  jobId: string;
  targetRepo: string;
  expectedHead: string;
  deadlineAt: string;
  candidateSha256: string;
  candidateFiles: StoredCandidate[];
  foregroundReceiptSha256: string;
  foregroundReceiptBase64: string;
  productionJobSha256: string;
  productionJobBase64: string;
};

export type AsyncProductionSealMaterial = {
  runId: string;
  jobId: string;
  targetRepo: string;
  expectedHead: string;
  deadlineAt: string;
  candidateSha256: string;
  candidateFiles: Array<{ path: string; sha256: string; bytes: Buffer }>;
  foregroundReceiptSha256: string;
  foregroundReceipt: Buffer;
  productionJobSha256: string;
  productionJob: Buffer;
  sealSha256: string;
};

type LifecycleStatus =
  | "awaiting-production"
  | "running-production"
  | "verified"
  | "failed"
  | "stale"
  | "cancelled";
type StoredEvent = {
  schemaVersion: "repo-async-production-event@v2";
  runId: string;
  version: number;
  sequence: number;
  previousVersion: number;
  occurredAt: string;
  status: Exclude<LifecycleStatus, "awaiting-production">;
  sealSha256: string;
  workerId?: string;
  fencingToken?: string;
  leaseExpiresAt?: string;
  resultRef?: string;
  resultSha256?: string;
  transactionParentDev?: number;
  transactionParentIno?: number;
  diagnostic?: string;
};

export type AsyncProductionView = {
  schemaVersion: "repo-async-production-view@v2";
  runId: string;
  jobId: string;
  status: LifecycleStatus;
  version: number;
  sequence: number;
  candidateSha256: string;
  sealSha256: string;
  deadlineAt: string;
  admissionEligible: false;
  nextAction: string;
  fencingToken?: string;
  leaseExpiresAt?: string;
  resultRef?: string;
  resultSha256?: string;
  transactionParentDev?: number;
  transactionParentIno?: number;
  diagnostic?: string;
  bindings: {
    expectedHead: string;
    targetRepo: string;
    foregroundReceiptSha256: string;
    productionJobSha256: string;
  };
};

function sha256(bytes: Uint8Array | string): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function assertRunId(runId: string): void {
  if (!RUN_ID.test(runId)) throw new Error("invalid-run-id");
}

function assertVersion(version: number): void {
  if (!Number.isSafeInteger(version) || version < 0)
    throw new Error("invalid-version");
}

function assertDate(name: string, value: Date): void {
  if (!Number.isFinite(value.getTime())) throw new Error(`invalid-${name}`);
}

function safeCandidatePath(path: string): string {
  const normalized = normalize(path);
  if (
    !path ||
    path.includes("\\") ||
    normalized === "." ||
    normalized !== path ||
    isAbsolute(normalized) ||
    normalized === ".." ||
    normalized.startsWith(`..${sep}`)
  ) {
    throw new Error(`unsafe-candidate-path:${path}`);
  }
  return normalized;
}

function recordPath(root: string, name: string): string {
  const runs = resolve(root, "runs");
  const rootPath = resolve(root);
  const local = relative(rootPath, runs);
  if (local !== "runs") throw new Error("invalid-state-root");
  const rootStat = lstatSync(rootPath);
  const runsStat = lstatSync(runs);
  if (
    !rootStat.isDirectory() ||
    rootStat.isSymbolicLink() ||
    !runsStat.isDirectory() ||
    runsStat.isSymbolicLink()
  ) {
    throw new Error("state-root-and-runs-must-be-real-directories");
  }
  return join(runs, name);
}

function readAnchoredRecord(path: string): Buffer {
  const descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  let bytes: Buffer | undefined;
  let failure: Error | undefined;
  try {
    const stat = fstatSync(descriptor);
    if (!stat.isFile() || stat.size > MAX_RECORD_BYTES)
      throw new Error("unsafe-or-oversized-lifecycle-record");
    bytes = readFileSync(descriptor);
  } catch (error) {
    failure =
      error instanceof Error
        ? error
        : new Error("lifecycle record read failed", { cause: error });
  }
  try {
    closeSync(descriptor);
  } catch (cleanupError) {
    const cleanup =
      cleanupError instanceof Error
        ? cleanupError
        : new Error("lifecycle record descriptor cleanup failed", {
            cause: cleanupError,
          });
    failure = failure
      ? new AggregateError(
          [failure, cleanup],
          "lifecycle record read and cleanup failed",
          { cause: cleanup },
        )
      : cleanup;
  }
  if (failure) throw failure;
  if (!bytes) throw new Error("lifecycle record read produced no bytes");
  return bytes;
}

function parseObject<T>(bytes: Uint8Array, description: string): T {
  try {
    const value = JSON.parse(Buffer.from(bytes).toString("utf8")) as unknown;
    if (value === null || typeof value !== "object" || Array.isArray(value))
      throw new Error("not an object");
    return value as T;
  } catch (error) {
    throw new Error(`invalid-${description}`, { cause: error });
  }
}

function buildSeal(input: AsyncSealInput): {
  bytes: Buffer;
  record: SealRecord;
} {
  assertRunId(input.runId);
  if (!RUN_ID.test(input.jobId)) throw new Error("invalid-job-id");
  if (!GIT_OID.test(input.expectedHead))
    throw new Error("invalid-seal-binding");
  safeCandidatePath(input.targetRepo);
  if (!Number.isFinite(Date.parse(input.deadlineAt)))
    throw new Error("invalid-deadline");
  if (input.candidateFiles.length === 0)
    throw new Error("candidate-files-empty");
  const paths = new Set<string>();
  const candidateFiles = input.candidateFiles
    .map((file): StoredCandidate => {
      const path = safeCandidatePath(file.path);
      if (paths.has(path)) throw new Error(`duplicate-candidate-path:${path}`);
      paths.add(path);
      const bytes = Buffer.from(file.bytes);
      return {
        path,
        sha256: sha256(bytes),
        contentBase64: bytes.toString("base64"),
      };
    })
    .sort((left, right) => left.path.localeCompare(right.path));
  const candidateSha256 = sha256(
    `${JSON.stringify(candidateFiles.map(({ path, sha256: digest }) => ({ path, sha256: digest })))}\n`,
  );
  const foregroundReceipt = Buffer.from(input.foregroundReceipt);
  const productionJob = Buffer.from(input.productionJob);
  if (foregroundReceipt.length === 0 || productionJob.length === 0)
    throw new Error("sealed-component-empty");
  const record: SealRecord = {
    schemaVersion: "repo-async-production-seal@v2",
    runId: input.runId,
    jobId: input.jobId,
    targetRepo: input.targetRepo,
    expectedHead: input.expectedHead,
    deadlineAt: new Date(input.deadlineAt).toISOString(),
    candidateSha256,
    candidateFiles,
    foregroundReceiptSha256: sha256(foregroundReceipt),
    foregroundReceiptBase64: foregroundReceipt.toString("base64"),
    productionJobSha256: sha256(productionJob),
    productionJobBase64: productionJob.toString("base64"),
  };
  return { bytes: Buffer.from(`${JSON.stringify(record)}\n`), record };
}

function errorChain(error: unknown): string {
  const messages: string[] = [];
  let current: unknown = error;
  for (let depth = 0; depth < 8 && current instanceof Error; depth += 1) {
    messages.push(current.message);
    current = current.cause;
  }
  return messages.join(": ");
}

function publish(root: string, outputPath: string, bytes: Buffer) {
  try {
    return publishWriterArtifact(root, outputPath, bytes);
  } catch (error) {
    throw new Error(errorChain(error), { cause: error });
  }
}

function validateSeal(record: SealRecord): void {
  if (
    record.schemaVersion !== "repo-async-production-seal@v2" ||
    !RUN_ID.test(record.runId) ||
    !RUN_ID.test(record.jobId) ||
    !GIT_OID.test(record.expectedHead) ||
    !SHA256.test(record.candidateSha256) ||
    !SHA256.test(record.foregroundReceiptSha256) ||
    !SHA256.test(record.productionJobSha256) ||
    !Array.isArray(record.candidateFiles) ||
    record.candidateFiles.length === 0
  ) {
    throw new Error("invalid-seal-contract");
  }
  const paths = new Set<string>();
  for (const file of record.candidateFiles) {
    const path = safeCandidatePath(file.path);
    if (
      paths.has(path) ||
      !SHA256.test(file.sha256) ||
      sha256(Buffer.from(file.contentBase64, "base64")) !== file.sha256
    ) {
      throw new Error("candidate-binding-mismatch");
    }
    paths.add(path);
  }
  const aggregate = sha256(
    `${JSON.stringify(record.candidateFiles.map(({ path, sha256: digest }) => ({ path, sha256: digest })))}\n`,
  );
  if (
    aggregate !== record.candidateSha256 ||
    sha256(Buffer.from(record.foregroundReceiptBase64, "base64")) !==
      record.foregroundReceiptSha256 ||
    sha256(Buffer.from(record.productionJobBase64, "base64")) !==
      record.productionJobSha256
  ) {
    throw new Error("seal-component-hash-mismatch");
  }
}

function sealRecord(
  root: string,
  runId: string,
): { record: SealRecord; sealSha256: string } {
  assertRunId(runId);
  const bytes = readAnchoredRecord(recordPath(root, `${runId}.seal.json`));
  const record = parseObject<SealRecord>(bytes, "seal-record");
  validateSeal(record);
  if (record.runId !== runId) throw new Error("seal-run-id-mismatch");
  return { record, sealSha256: sha256(bytes) };
}

function eventName(runId: string, version: number): string {
  return `${runId}.event.${String(version).padStart(12, "0")}.json`;
}

function readEvents(root: string, runId: string): StoredEvent[] {
  const runsPath = recordPath(root, ".");
  return readdirSync(runsPath)
    .filter(
      (name) => name.startsWith(`${runId}.event.`) && name.endsWith(".json"),
    )
    .sort()
    .map((name, index) => {
      const event = parseObject<StoredEvent>(
        readAnchoredRecord(join(runsPath, name)),
        "event-record",
      );
      const version = index + 1;
      if (
        name !== eventName(runId, version) ||
        event.schemaVersion !== "repo-async-production-event@v2" ||
        event.runId !== runId ||
        event.version !== version ||
        event.sequence !== version ||
        event.previousVersion !== version - 1
      )
        throw new Error("non-monotonic-event-log");
      return event;
    });
}

function view(
  record: SealRecord,
  sealSha256: string,
  event: StoredEvent | undefined,
  now: Date,
): AsyncProductionView {
  assertDate("inspection-time", now);
  const status = event?.status ?? "awaiting-production";
  const expired =
    status === "running-production" &&
    event?.leaseExpiresAt !== undefined &&
    now.getTime() >= Date.parse(event.leaseExpiresAt);
  return {
    schemaVersion: "repo-async-production-view@v2",
    runId: record.runId,
    jobId: record.jobId,
    status,
    version: event?.version ?? 0,
    sequence: event?.sequence ?? 0,
    candidateSha256: record.candidateSha256,
    sealSha256,
    deadlineAt: record.deadlineAt,
    admissionEligible: false,
    nextAction:
      status === "verified"
        ? "foreground-admission-required"
        : expired
          ? "verification-lease-expired-reclaimable"
          : status === "awaiting-production"
            ? "claim-production-job"
            : status === "running-production"
              ? "await-production-result"
              : "repair-or-close",
    ...(event?.fencingToken === undefined
      ? {}
      : { fencingToken: event.fencingToken }),
    ...(event?.leaseExpiresAt === undefined
      ? {}
      : { leaseExpiresAt: event.leaseExpiresAt }),
    ...(event?.resultRef === undefined ? {} : { resultRef: event.resultRef }),
    ...(event?.resultSha256 === undefined
      ? {}
      : { resultSha256: event.resultSha256 }),
    ...(event?.transactionParentDev === undefined
      ? {}
      : { transactionParentDev: event.transactionParentDev }),
    ...(event?.transactionParentIno === undefined
      ? {}
      : { transactionParentIno: event.transactionParentIno }),
    ...(event?.diagnostic === undefined
      ? {}
      : { diagnostic: event.diagnostic }),
    bindings: {
      expectedHead: record.expectedHead,
      targetRepo: record.targetRepo,
      foregroundReceiptSha256: record.foregroundReceiptSha256,
      productionJobSha256: record.productionJobSha256,
    },
  };
}

export function sealAsyncProductionRun(root: string, input: AsyncSealInput) {
  const { bytes, record } = buildSeal(input);
  const outputPath = recordPath(root, `${record.runId}.seal.json`);
  const publication = publish(root, outputPath, bytes);
  const reopened = sealRecord(root, record.runId);
  if (reopened.record.candidateSha256 !== record.candidateSha256)
    throw new Error("published-seal-readback-mismatch");
  return {
    schemaVersion: "repo-async-production-foreground-outcome@v2" as const,
    status: "awaiting-production" as const,
    runId: record.runId,
    version: 0,
    candidateSha256: record.candidateSha256,
    sealSha256: reopened.sealSha256,
    jobId: record.jobId,
    admissionEligible: false as const,
    publication: publication.writerOutcome,
    recovery: publication.recoveryOutcome,
  };
}

export function inspectAsyncProductionRun(
  root: string,
  runId: string,
  now = new Date(),
): AsyncProductionView {
  const seal = sealRecord(root, runId);
  const events = readEvents(root, runId);
  const latest = events.at(-1);
  if (latest && latest.sealSha256 !== seal.sealSha256)
    throw new Error("event-seal-hash-mismatch");
  return view(seal.record, seal.sealSha256, latest, now);
}

export function openAsyncProductionSeal(
  root: string,
  runId: string,
  expectedSealSha256: string,
): AsyncProductionSealMaterial {
  const { record, sealSha256 } = sealRecord(root, runId);
  if (!SHA256.test(expectedSealSha256) || sealSha256 !== expectedSealSha256)
    throw new Error("seal-hash-mismatch");
  return {
    runId: record.runId,
    jobId: record.jobId,
    targetRepo: record.targetRepo,
    expectedHead: record.expectedHead,
    deadlineAt: record.deadlineAt,
    candidateSha256: record.candidateSha256,
    candidateFiles: record.candidateFiles.map((candidate) => ({
      path: candidate.path,
      sha256: candidate.sha256,
      bytes: Buffer.from(candidate.contentBase64, "base64"),
    })),
    foregroundReceiptSha256: record.foregroundReceiptSha256,
    foregroundReceipt: Buffer.from(record.foregroundReceiptBase64, "base64"),
    productionJobSha256: record.productionJobSha256,
    productionJob: Buffer.from(record.productionJobBase64, "base64"),
    sealSha256,
  };
}

function publishEvent(
  root: string,
  event: StoredEvent,
  now: Date,
): AsyncProductionView {
  publish(
    root,
    recordPath(root, eventName(event.runId, event.version)),
    Buffer.from(`${JSON.stringify(event)}\n`),
  );
  return inspectAsyncProductionRun(root, event.runId, now);
}

function assertExpected(
  view: AsyncProductionView,
  expectedVersion: number,
): void {
  assertVersion(expectedVersion);
  if (view.version !== expectedVersion)
    throw new Error(`cas-version-mismatch:${expectedVersion}->${view.version}`);
}

export function claimAsyncProductionRun(
  root: string,
  input: AsyncClaimInput,
): AsyncProductionView {
  assertDate("claim-time", input.now);
  assertDate("lease-expiry", input.leaseExpiresAt);
  if (input.leaseExpiresAt.getTime() <= input.now.getTime())
    throw new Error("lease-must-expire-after-claim");
  if (!RUN_ID.test(input.workerId) || !RUN_ID.test(input.fencingToken))
    throw new Error("invalid-worker-or-fencing-token");
  const current = inspectAsyncProductionRun(root, input.runId, input.now);
  assertExpected(current, input.expectedVersion);
  if (
    !SHA256.test(input.expectedSealSha256) ||
    input.expectedSealSha256 !== current.sealSha256
  )
    throw new Error("seal-hash-mismatch");
  if (input.now.getTime() >= Date.parse(current.deadlineAt))
    throw new Error("run-deadline-expired");
  if (input.leaseExpiresAt.getTime() > Date.parse(current.deadlineAt))
    throw new Error("lease-exceeds-run-deadline");
  const expired =
    current.status === "running-production" &&
    current.leaseExpiresAt !== undefined &&
    input.now.getTime() >= Date.parse(current.leaseExpiresAt);
  if (current.status !== "awaiting-production" && !expired)
    throw new Error(`job-not-claimable:${current.status}`);
  return publishEvent(
    root,
    {
      schemaVersion: "repo-async-production-event@v2",
      runId: input.runId,
      version: input.expectedVersion + 1,
      sequence: input.expectedVersion + 1,
      previousVersion: input.expectedVersion,
      occurredAt: input.now.toISOString(),
      status: "running-production",
      sealSha256: current.sealSha256,
      workerId: input.workerId,
      fencingToken: input.fencingToken,
      leaseExpiresAt: input.leaseExpiresAt.toISOString(),
    },
    input.now,
  );
}

export function finishAsyncProductionRun(
  root: string,
  input: AsyncFinishInput,
): AsyncProductionView {
  assertDate("finish-time", input.now);
  if (!["verified", "failed", "stale"].includes(input.terminalStatus))
    throw new Error("invalid-terminal-status");
  if (!RUN_ID.test(input.fencingToken) || !SHA256.test(input.resultSha256)) {
    throw new Error("invalid-finish-binding");
  }
  const hasTransactionParent =
    input.transactionParentDev !== undefined ||
    input.transactionParentIno !== undefined;
  if (
    hasTransactionParent &&
    (!Number.isSafeInteger(input.transactionParentDev) ||
      !Number.isSafeInteger(input.transactionParentIno) ||
      Number(input.transactionParentDev) < 0 ||
      Number(input.transactionParentIno) < 0)
  )
    throw new Error("invalid-transaction-parent-binding");
  safeCandidatePath(input.resultRef);
  const current = inspectAsyncProductionRun(root, input.runId, input.now);
  assertExpected(current, input.expectedVersion);
  if (input.now.getTime() >= Date.parse(current.deadlineAt))
    throw new Error("run-deadline-expired");
  if (
    current.status !== "running-production" ||
    current.leaseExpiresAt === undefined
  ) {
    throw new Error(`job-not-running:${current.status}`);
  }
  if (current.fencingToken !== input.fencingToken)
    throw new Error("fencing-token-mismatch");
  if (input.now.getTime() >= Date.parse(current.leaseExpiresAt))
    throw new Error("expired-fencing-token");
  return publishEvent(
    root,
    {
      schemaVersion: "repo-async-production-event@v2",
      runId: input.runId,
      version: input.expectedVersion + 1,
      sequence: input.expectedVersion + 1,
      previousVersion: input.expectedVersion,
      occurredAt: input.now.toISOString(),
      status: input.terminalStatus,
      sealSha256: current.sealSha256,
      fencingToken: input.fencingToken,
      resultRef: input.resultRef,
      resultSha256: input.resultSha256,
      ...(hasTransactionParent
        ? {
            transactionParentDev: input.transactionParentDev,
            transactionParentIno: input.transactionParentIno,
          }
        : {}),
    },
    input.now,
  );
}

export function cancelAsyncProductionRun(
  root: string,
  input: AsyncCancelInput,
): AsyncProductionView {
  assertDate("cancel-time", input.now);
  if (!input.reason.trim()) throw new Error("cancel-reason-required");
  const current = inspectAsyncProductionRun(root, input.runId, input.now);
  assertExpected(current, input.expectedVersion);
  if (
    current.status !== "awaiting-production" &&
    current.status !== "running-production"
  ) {
    throw new Error(`job-not-cancellable:${current.status}`);
  }
  return publishEvent(
    root,
    {
      schemaVersion: "repo-async-production-event@v2",
      runId: input.runId,
      version: input.expectedVersion + 1,
      sequence: input.expectedVersion + 1,
      previousVersion: input.expectedVersion,
      occurredAt: input.now.toISOString(),
      status: "cancelled",
      sealSha256: current.sealSha256,
      diagnostic: input.reason.trim(),
    },
    input.now,
  );
}
