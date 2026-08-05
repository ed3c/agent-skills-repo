import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { lstatSync, mkdirSync } from "node:fs";
import { isAbsolute, join, normalize, relative, sep } from "node:path";
import { readAnchoredArtifact } from "./anchored-artifact-read";
import {
  cancelAsyncProductionRun,
  inspectAsyncProductionRun,
  openAsyncProductionSeal,
  sealAsyncProductionRun,
} from "./async-job-lifecycle";
import { publishWriterArtifact } from "./writer-publication";
import { parseAsyncWorkerJob } from "./async-worker-carrier";
import {
  parseJsonRecord,
  type AsyncFacadeAdmitCompletion,
  type AsyncFacadeAdmitRequest,
  type AsyncFacadeCancelCompletion,
  type AsyncFacadeCancelRequest,
  type AsyncFacadeCompletion,
  type AsyncFacadeDependencies,
  type AsyncFacadeInspectCompletion,
  type AsyncFacadeInspectRequest,
  type AsyncFacadeRequest,
  type AsyncFacadeStartCompletion,
  type AsyncFacadeStartRequest,
} from "./async-admission-contract";
import {
  assertForegroundReceipt,
  assertWorkerResult,
} from "./async-admission-verifier";

export { parseAsyncFacadeRequest } from "./async-admission-contract";
export type {
  AsyncFacadeAdmitCompletion,
  AsyncFacadeAdmitRequest,
  AsyncFacadeCancelCompletion,
  AsyncFacadeCancelRequest,
  AsyncFacadeCompletion,
  AsyncFacadeDependencies,
  AsyncFacadeInspectCompletion,
  AsyncFacadeInspectRequest,
  AsyncFacadeRequest,
  AsyncFacadeStartCompletion,
  AsyncFacadeStartRequest,
} from "./async-admission-contract";

const MAX_INPUT_BYTES = 16 * 1024 * 1024;
const GIT_OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/u;

function sha256(bytes: Uint8Array | string): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function safeRef(value: string, label: string): string {
  const normalized = normalize(value);
  if (
    !value ||
    value.includes("\\") ||
    normalized !== value ||
    normalized === "." ||
    normalized === ".." ||
    normalized.startsWith(`..${sep}`) ||
    isAbsolute(normalized)
  ) {
    throw new Error(`${label}-unsafe`);
  }
  return normalized;
}

function requiredBytes(root: string, ref: string, label: string): Buffer {
  const bytes = readAnchoredArtifact(
    root,
    safeRef(ref, label),
    MAX_INPUT_BYTES,
    label,
  );
  if (!bytes) throw new Error(`${label}-missing`);
  return bytes;
}

function ensureRealDirectory(root: string, name: string): void {
  const path = join(root, name);
  try {
    mkdirSync(path, { mode: 0o700 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  }
  const stat = lstatSync(path);
  if (!stat.isDirectory() || stat.isSymbolicLink())
    throw new Error(`${name}-must-be-real-directory`);
}

function currentHead(sourceRoot: string): string {
  const result = spawnSync("git", ["rev-parse", "HEAD"], {
    cwd: sourceRoot,
    encoding: "utf8",
    timeout: 2_000,
  });
  if (result.status !== 0)
    throw new Error(`head-unreadable:${result.stderr.trim()}`);
  return result.stdout.trim();
}

function start(
  stateRoot: string,
  sourceRoot: string,
  request: AsyncFacadeStartRequest,
  dependencies: AsyncFacadeDependencies,
): AsyncFacadeStartCompletion {
  if (!GIT_OID.test(request.expected_head))
    throw new Error("expected-head-invalid");
  const observedHead = dependencies.head(sourceRoot);
  if (observedHead !== request.expected_head)
    throw new Error(`head-stale:${request.expected_head}->${observedHead}`);
  const targetRepo = safeRef(request.target_repo, "target-repo");
  if (
    request.candidate_refs.length === 0 ||
    request.candidate_refs.length > 256
  ) {
    throw new Error("candidate-refs-invalid");
  }
  const candidateFiles = request.candidate_refs.map((candidateRef) => {
    const path = safeRef(candidateRef, "candidate-ref");
    const local = relative(targetRepo, path);
    if (
      local === "" ||
      local === ".." ||
      local.startsWith(`..${sep}`) ||
      isAbsolute(local)
    ) {
      throw new Error("candidate-outside-target-repo");
    }
    return { path, bytes: requiredBytes(sourceRoot, path, "candidate") };
  });
  const foregroundReceipt = requiredBytes(
    sourceRoot,
    request.foreground_receipt_ref,
    "foreground-receipt",
  );
  assertForegroundReceipt(
    parseJsonRecord(foregroundReceipt, "foreground-receipt"),
    request.run_id,
    request.expected_head,
  );
  const productionJob = requiredBytes(
    sourceRoot,
    request.production_job_ref,
    "production-job",
  );
  try {
    parseAsyncWorkerJob(productionJob);
  } catch (error) {
    throw new Error("production-job-binding-invalid", { cause: error });
  }
  const sealed = sealAsyncProductionRun(stateRoot, {
    runId: request.run_id,
    jobId: request.job_id,
    targetRepo,
    expectedHead: request.expected_head,
    deadlineAt: request.deadline_at,
    candidateFiles,
    foregroundReceipt,
    productionJob,
  });
  if (sealed.version !== 0) throw new Error("initial-version-invalid");
  return {
    schema_version: "repo-async-production-facade-completion@v1",
    action: "start",
    status: sealed.status,
    run_id: sealed.runId,
    version: sealed.version,
    seal_sha256: sealed.sealSha256,
    candidate_sha256: sealed.candidateSha256,
    publication: sealed.publication,
    admission_eligible: false,
  };
}

function inspect(
  stateRoot: string,
  request: AsyncFacadeInspectRequest,
): AsyncFacadeInspectCompletion {
  const observedAt = new Date(request.observed_at);
  if (
    !Number.isFinite(observedAt.getTime()) ||
    observedAt.toISOString() !== request.observed_at
  ) {
    throw new Error("observed-at-invalid");
  }
  const view = inspectAsyncProductionRun(stateRoot, request.run_id, observedAt);
  return {
    schema_version: "repo-async-production-facade-completion@v1",
    action: "inspect",
    status: view.status,
    run_id: view.runId,
    version: view.version,
    seal_sha256: view.sealSha256,
    candidate_sha256: view.candidateSha256,
    next_action: view.nextAction,
    admission_eligible: false,
  };
}

function cancel(
  stateRoot: string,
  request: AsyncFacadeCancelRequest,
): AsyncFacadeCancelCompletion {
  const observedAt = new Date(request.observed_at);
  if (
    !Number.isFinite(observedAt.getTime()) ||
    observedAt.toISOString() !== request.observed_at
  ) {
    throw new Error("observed-at-invalid");
  }
  const view = cancelAsyncProductionRun(stateRoot, {
    runId: request.run_id,
    expectedVersion: request.expected_version,
    reason: request.reason,
    now: observedAt,
  });
  return {
    schema_version: "repo-async-production-facade-completion@v1",
    action: "cancel",
    status: "cancelled",
    run_id: view.runId,
    version: view.version,
    seal_sha256: view.sealSha256,
    candidate_sha256: view.candidateSha256,
    diagnostic: view.diagnostic ?? request.reason.trim(),
    next_action: "closed",
    admission_eligible: false,
  };
}

function admit(
  stateRoot: string,
  sourceRoot: string,
  request: AsyncFacadeAdmitRequest,
  dependencies: AsyncFacadeDependencies,
): AsyncFacadeAdmitCompletion {
  const observedAt = new Date(request.observed_at);
  if (
    !Number.isFinite(observedAt.getTime()) ||
    observedAt.toISOString() !== request.observed_at
  ) {
    throw new Error("observed-at-invalid");
  }
  const view = inspectAsyncProductionRun(stateRoot, request.run_id, observedAt);
  if (view.version !== request.expected_version) {
    throw new Error(
      `cas-version-mismatch:${request.expected_version}->${view.version}`,
    );
  }
  if (
    view.status !== "verified" ||
    view.sealSha256 !== request.expected_seal_sha256
  ) {
    throw new Error("run-not-currently-verified");
  }
  const seal = openAsyncProductionSeal(
    stateRoot,
    request.run_id,
    request.expected_seal_sha256,
  );
  const observedHead = dependencies.head(sourceRoot);
  if (observedHead !== seal.expectedHead)
    throw new Error(`head-stale:${seal.expectedHead}->${observedHead}`);
  for (const candidate of seal.candidateFiles) {
    const current = readAnchoredArtifact(
      sourceRoot,
      candidate.path,
      MAX_INPUT_BYTES,
      "current-candidate",
    );
    if (!current || sha256(current) !== candidate.sha256)
      throw new Error(`candidate-stale:${candidate.path}`);
  }
  assertForegroundReceipt(
    parseJsonRecord(seal.foregroundReceipt, "foreground-receipt"),
    seal.runId,
    seal.expectedHead,
  );
  const job = parseAsyncWorkerJob(seal.productionJob);
  if (!view.resultRef || !view.resultSha256)
    throw new Error("verified-result-binding-missing");
  const resultBytes = readAnchoredArtifact(
    stateRoot,
    view.resultRef,
    MAX_INPUT_BYTES,
    "worker-result",
  );
  if (!resultBytes || sha256(resultBytes) !== view.resultSha256)
    throw new Error("worker-result-hash-stale");
  const isolation = assertWorkerResult(
    parseJsonRecord(resultBytes, "worker-result"),
    job,
    view,
    seal.expectedHead,
  );
  ensureRealDirectory(stateRoot, "admissions");
  const admissionRef = `admissions/${seal.runId}.json`;
  const admission = Buffer.from(
    `${JSON.stringify({
      schema_version: "repo-async-production-admission@v1",
      status: "admitted",
      run_id: seal.runId,
      version: view.version,
      seal_sha256: seal.sealSha256,
      candidate_sha256: seal.candidateSha256,
      foreground_receipt_sha256: seal.foregroundReceiptSha256,
      production_job_sha256: seal.productionJobSha256,
      result_ref: view.resultRef,
      result_sha256: view.resultSha256,
      expected_head: seal.expectedHead,
      current_head: observedHead,
      target_repo: seal.targetRepo,
      isolation_mode: isolation.isolationMode,
      degraded: isolation.degraded,
      admission_eligible: true,
    })}\n`,
  );
  const publication = publishWriterArtifact(
    stateRoot,
    join(stateRoot, admissionRef),
    admission,
  ).writerOutcome;
  const reopened = readAnchoredArtifact(
    stateRoot,
    admissionRef,
    MAX_INPUT_BYTES,
    "admission",
  );
  if (!reopened || !reopened.equals(admission))
    throw new Error("admission-readback-mismatch");
  return {
    schema_version: "repo-async-production-facade-completion@v1",
    action: "admit",
    status: publication === "published" ? "admitted" : "already-admitted",
    run_id: seal.runId,
    version: view.version,
    admission_ref: admissionRef,
    admission_sha256: sha256(admission),
    publication,
    admission_eligible: publication === "published",
  };
}

export function executeAsyncProductionFacade(
  stateRoot: string,
  sourceRoot: string,
  request: AsyncFacadeStartRequest,
  dependencies?: AsyncFacadeDependencies,
): AsyncFacadeStartCompletion;
export function executeAsyncProductionFacade(
  stateRoot: string,
  sourceRoot: string,
  request: AsyncFacadeInspectRequest,
  dependencies?: AsyncFacadeDependencies,
): AsyncFacadeInspectCompletion;
export function executeAsyncProductionFacade(
  stateRoot: string,
  sourceRoot: string,
  request: AsyncFacadeCancelRequest,
  dependencies?: AsyncFacadeDependencies,
): AsyncFacadeCancelCompletion;
export function executeAsyncProductionFacade(
  stateRoot: string,
  sourceRoot: string,
  request: AsyncFacadeAdmitRequest,
  dependencies?: AsyncFacadeDependencies,
): AsyncFacadeAdmitCompletion;
export function executeAsyncProductionFacade(
  stateRoot: string,
  sourceRoot: string,
  request: AsyncFacadeRequest,
  dependencies?: AsyncFacadeDependencies,
): AsyncFacadeCompletion;
export function executeAsyncProductionFacade(
  stateRoot: string,
  sourceRoot: string,
  request: AsyncFacadeRequest,
  dependencies: AsyncFacadeDependencies = { head: currentHead },
): AsyncFacadeCompletion {
  if (request.schema_version !== "repo-async-production-facade-request@v1")
    throw new Error("facade-request-invalid");
  if (request.action === "start")
    return start(stateRoot, sourceRoot, request, dependencies);
  if (request.action === "inspect") return inspect(stateRoot, request);
  if (request.action === "cancel") return cancel(stateRoot, request);
  return admit(stateRoot, sourceRoot, request, dependencies);
}
