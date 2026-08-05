import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import {
  closeSync,
  constants,
  existsSync,
  fstatSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  readSync,
  realpathSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import {
  dirname,
  isAbsolute,
  join,
  normalize,
  relative,
  resolve,
  sep,
} from "node:path";
import {
  runOwnedProfileCommand,
  type OwnedProfileCommandResult,
} from "../../../../../skills/repo-neural-perception/scripts/owned-profile-command";
import {
  claimAsyncProductionRun,
  finishAsyncProductionRun,
  inspectAsyncProductionRun,
  openAsyncProductionSeal,
  type AsyncProductionSealMaterial,
} from "./async-job-lifecycle";
import {
  publishWriterArtifact,
  removeWriterArtifact,
  type WriterParentIdentity,
} from "./writer-publication";

const SHA256 = /^[a-f0-9]{64}$/u;
const IDENTIFIER = /^[a-z0-9][a-z0-9._-]{0,127}$/u;
const MAX_REQUEST_BYTES = 64 * 1024;
// 2.5s process termination/drain + 2s snapshot cleanup + 5s Git reopen +
// 3.6s private/public anchored-writer budgets + 1.9s scheduling/finish reserve.
const MIN_LEASE_FINISH_BUDGET_MS = 15_000;
const SNAPSHOT_CLEANUP_TIMEOUT_MS = 2_000;

export type AsyncWorkerRequest = {
  schema_version: "repo-async-production-worker-request@v1";
  run_id: string;
  expected_version: number;
  expected_seal_sha256: string;
  worker_id: string;
  fencing_token: string;
  lease_ms: number;
};

export type AsyncWorkerJob = {
  schema_version: "repo-async-production-worker-job@v1";
  command: string[];
  executable_sha256: string;
  driver_path: string;
  driver_sha256: string;
  timeout_ms: number;
  result_ref: string;
  allow_degraded: boolean;
  expected_final_contract: "gcr-sealed-background-review-stage-final@v1";
};

type ReviewerFinal = {
  schema_version: "gcr-sealed-background-review-stage-final@v1";
  status: "passed" | "failed";
  summary: string;
  findings: Array<{
    severity: "blocker" | "warning" | "info";
    code: string;
    summary: string;
  }>;
};

export type AsyncWorkerCompletion = {
  schema_version: "repo-async-production-worker-completion@v1";
  status: "verified" | "failed" | "stale" | "cancelled";
  run_id: string;
  result_ref?: string;
  result_sha256?: string;
  process_reaped: boolean;
  timer_cleared: boolean;
  snapshot_removed: boolean;
  diagnostic?: string;
};

export type AsyncWorkerProgress = {
  schema_version: "repo-async-production-worker-progress@v2";
  run_id: string;
  expected_version: number;
  seal_sha256: string;
  fencing_token: string;
  sequence: number;
  stage: "materialize" | "claim" | "review" | "finish";
  state: "started" | "finished";
  status?: "passed" | "failed" | "stale" | "cancelled";
  admission_eligible: false;
};

export type AsyncWorkerProgressSink = (event: AsyncWorkerProgress) => void;

function progress(
  sink: AsyncWorkerProgressSink,
  request: AsyncWorkerRequest,
  stage: AsyncWorkerProgress["stage"],
  state: AsyncWorkerProgress["state"],
  status?: AsyncWorkerProgress["status"],
): void {
  const stages: AsyncWorkerProgress["stage"][] = [
    "materialize",
    "claim",
    "review",
    "finish",
  ];
  const sequence = stages.indexOf(stage) * 2 + (state === "started" ? 1 : 2);
  sink({
    schema_version: "repo-async-production-worker-progress@v2",
    run_id: request.run_id,
    expected_version: request.expected_version,
    seal_sha256: request.expected_seal_sha256,
    fencing_token: request.fencing_token,
    sequence,
    stage,
    state,
    ...(status ? { status } : {}),
    admission_eligible: false,
  });
}

function sha256(bytes: Uint8Array | string): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${label}-must-be-object`);
  return value as Record<string, unknown>;
}

function exactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
  label: string,
): void {
  const keys = Object.keys(value).sort();
  const allowed = [...expected].sort();
  if (JSON.stringify(keys) !== JSON.stringify(allowed))
    throw new Error(`${label}-fields-invalid`);
}

function integer(
  value: unknown,
  label: string,
  minimum: number,
  maximum: number,
): number {
  if (
    !Number.isSafeInteger(value) ||
    (value as number) < minimum ||
    (value as number) > maximum
  )
    throw new Error(`${label}-invalid`);
  return value as number;
}

function safeRelativePath(path: string, label: string): string {
  const normalized = normalize(path);
  if (
    !path ||
    path.includes("\\") ||
    normalized === "." ||
    normalized !== path ||
    isAbsolute(path) ||
    normalized === ".." ||
    normalized.startsWith(`..${sep}`)
  )
    throw new Error(`${label}-unsafe`);
  return normalized;
}

function readBoundedRegularFile(path: string, maximum: number): Buffer {
  const descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = fstatSync(descriptor);
    if (!stat.isFile() || stat.size > maximum)
      throw new Error("input-not-bounded-regular-file");
    const snapshot = Buffer.alloc(stat.size + 1);
    let offset = 0;
    while (offset < snapshot.length) {
      const count = readSync(
        descriptor,
        snapshot,
        offset,
        snapshot.length - offset,
        null,
      );
      if (count === 0) break;
      offset += count;
    }
    if (offset !== stat.size) throw new Error("input-changed-during-read");
    return snapshot.subarray(0, offset);
  } finally {
    closeSync(descriptor);
  }
}

export function parseAsyncWorkerRequest(bytes: Uint8Array): AsyncWorkerRequest {
  if (bytes.byteLength > MAX_REQUEST_BYTES)
    throw new Error("request-too-large");
  const input = object(
    JSON.parse(Buffer.from(bytes).toString("utf8")),
    "request",
  );
  exactKeys(
    input,
    [
      "schema_version",
      "run_id",
      "expected_version",
      "expected_seal_sha256",
      "worker_id",
      "fencing_token",
      "lease_ms",
    ],
    "request",
  );
  if (
    input.schema_version !== "repo-async-production-worker-request@v1" ||
    typeof input.run_id !== "string" ||
    !IDENTIFIER.test(input.run_id) ||
    typeof input.expected_seal_sha256 !== "string" ||
    !SHA256.test(input.expected_seal_sha256) ||
    typeof input.worker_id !== "string" ||
    !IDENTIFIER.test(input.worker_id) ||
    typeof input.fencing_token !== "string" ||
    !IDENTIFIER.test(input.fencing_token)
  )
    throw new Error("request-contract-invalid");
  return {
    schema_version: input.schema_version,
    run_id: input.run_id,
    expected_version: integer(
      input.expected_version,
      "expected-version",
      0,
      Number.MAX_SAFE_INTEGER,
    ),
    expected_seal_sha256: input.expected_seal_sha256,
    worker_id: input.worker_id,
    fencing_token: input.fencing_token,
    lease_ms: integer(input.lease_ms, "lease-ms", 100, 3_600_000),
  };
}

export function parseAsyncWorkerJob(bytes: Uint8Array): AsyncWorkerJob {
  const input = object(
    JSON.parse(Buffer.from(bytes).toString("utf8")),
    "production-job",
  );
  exactKeys(
    input,
    [
      "schema_version",
      "command",
      "executable_sha256",
      "driver_path",
      "driver_sha256",
      "timeout_ms",
      "result_ref",
      "allow_degraded",
      "expected_final_contract",
    ],
    "production-job",
  );
  if (
    input.schema_version !== "repo-async-production-worker-job@v1" ||
    !Array.isArray(input.command) ||
    input.command.length === 0 ||
    input.command.length > 64 ||
    !input.command.every(
      (part) => typeof part === "string" && part.length <= 8192,
    ) ||
    typeof input.executable_sha256 !== "string" ||
    !SHA256.test(input.executable_sha256) ||
    typeof input.driver_path !== "string" ||
    typeof input.driver_sha256 !== "string" ||
    !SHA256.test(input.driver_sha256) ||
    typeof input.result_ref !== "string" ||
    !input.result_ref.startsWith("results/") ||
    typeof input.allow_degraded !== "boolean" ||
    input.expected_final_contract !==
      "gcr-sealed-background-review-stage-final@v1"
  )
    throw new Error("production-job-contract-invalid");
  const command = input.command as string[];
  if (!isAbsolute(command[0] ?? ""))
    throw new Error("production-job-executable-must-be-absolute");
  const driverPath = safeRelativePath(input.driver_path, "driver-path");
  if (command[1] !== driverPath)
    throw new Error("production-job-driver-command-mismatch");
  return {
    schema_version: input.schema_version,
    command,
    executable_sha256: input.executable_sha256,
    driver_path: driverPath,
    driver_sha256: input.driver_sha256,
    timeout_ms: integer(input.timeout_ms, "job-timeout-ms", 50, 3_600_000),
    result_ref: safeRelativePath(input.result_ref, "result-ref"),
    allow_degraded: input.allow_degraded,
    expected_final_contract: input.expected_final_contract,
  };
}

function parseJob(seal: AsyncProductionSealMaterial): AsyncWorkerJob {
  return parseAsyncWorkerJob(seal.productionJob);
}

export function minimumAsyncWorkerLeaseMs(
  seal: AsyncProductionSealMaterial,
): number {
  return parseJob(seal).timeout_ms + MIN_LEASE_FINISH_BUDGET_MS;
}

function executableIdentity(path: string, expectedSha256: string) {
  if (!isAbsolute(path)) throw new Error("executable-path-not-absolute");
  const realPath = realpathSync(path);
  if (!statSync(realPath).isFile())
    throw new Error("executable-not-regular-file");
  const digest = sha256(readFileSync(realPath));
  if (digest !== expectedSha256) throw new Error("executable-hash-mismatch");
  return { path, real_path: realPath, sha256: digest };
}

function driverIdentity(target: string, job: AsyncWorkerJob) {
  if (job.driver_path.startsWith("-"))
    throw new Error("driver-path-cannot-be-command-option");
  const path = join(target, job.driver_path);
  const local = relative(target, path);
  if (local.startsWith(`..${sep}`) || isAbsolute(local))
    throw new Error("driver-path-escaped-target");
  const digest = sha256(readBoundedRegularFile(path, 4 * 1024 * 1024));
  if (digest !== job.driver_sha256) throw new Error("driver-hash-mismatch");
  return { path: job.driver_path, sha256: digest };
}

function requiredExecutable(name: string): string {
  const path = Bun.which(name);
  if (!path) throw new Error(`required-executable-absent:${name}`);
  return realpathSync(path);
}

function gitHead(gitPath: string, sourceRoot: string): string {
  const result = spawnSync(gitPath, ["rev-parse", "HEAD"], {
    cwd: sourceRoot,
    encoding: "utf8",
    timeout: 5_000,
    killSignal: "SIGKILL",
  });
  const head = result.stdout.trim();
  if (result.status !== 0 || !/^[a-f0-9]{40}$/u.test(head))
    throw new Error(`source-head-unreadable:${result.stderr.trim()}`);
  return head;
}

function reopenedSourceBinding(
  sourceRoot: string,
  expectedHead: string,
): {
  expected_head: string;
  current_head: string | null;
  binding: "current" | "stale" | "unreadable";
} {
  try {
    const currentHead = gitHead(requiredExecutable("git"), sourceRoot);
    return {
      expected_head: expectedHead,
      current_head: currentHead,
      binding: currentHead === expectedHead ? "current" : "stale",
    };
  } catch {
    return {
      expected_head: expectedHead,
      current_head: null,
      binding: "unreadable",
    };
  }
}

function diagnosticChain(error: unknown): string {
  const messages: string[] = [];
  let current = error;
  for (let depth = 0; depth < 8 && current instanceof Error; depth += 1) {
    messages.push(current.message);
    current = current.cause;
  }
  return messages.join(": ") || String(error);
}

function ensureRealDirectory(path: string, label: string): string {
  const resolved = resolve(path);
  const stat = lstatSync(resolved);
  if (!stat.isDirectory() || stat.isSymbolicLink())
    throw new Error(`${label}-must-be-real-directory`);
  return realpathSync(resolved);
}

function safeOverlayPath(snapshot: string, candidatePath: string): string {
  const safe = safeRelativePath(candidatePath, "candidate-path");
  const parts = safe.split(sep);
  let parent = snapshot;
  for (const part of parts.slice(0, -1)) {
    parent = join(parent, part);
    try {
      const stat = lstatSync(parent);
      if (!stat.isDirectory() || stat.isSymbolicLink())
        throw new Error(`candidate-parent-not-real-directory:${part}`);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
      mkdirSync(parent, { mode: 0o700 });
    }
  }
  const target = join(parent, parts.at(-1) as string);
  const local = relative(snapshot, target);
  if (local.startsWith(`..${sep}`) || isAbsolute(local))
    throw new Error("candidate-path-escaped-snapshot");
  return target;
}

function overlayCandidates(
  snapshot: string,
  seal: AsyncProductionSealMaterial,
) {
  for (const candidate of seal.candidateFiles) {
    if (sha256(candidate.bytes) !== candidate.sha256)
      throw new Error("candidate-reopen-hash-mismatch");
    const output = safeOverlayPath(snapshot, candidate.path);
    const pending = `${output}.async-worker-pending`;
    const descriptor = openSync(
      pending,
      constants.O_WRONLY |
        constants.O_CREAT |
        constants.O_EXCL |
        constants.O_NOFOLLOW,
      0o600,
    );
    try {
      writeFileSync(descriptor, candidate.bytes);
    } finally {
      closeSync(descriptor);
    }
    renameSync(pending, output);
    if (
      sha256(readBoundedRegularFile(output, 16 * 1024 * 1024)) !==
      candidate.sha256
    )
      throw new Error("candidate-overlay-readback-mismatch");
  }
}

async function materializeSnapshot(
  sourceRoot: string,
  seal: AsyncProductionSealMaterial,
  temporaryRoot: string,
): Promise<string> {
  const gitPath = requiredExecutable("git");
  const tarPath = requiredExecutable("tar");
  const archive = join(temporaryRoot, "source.tar");
  const snapshot = join(temporaryRoot, "snapshot");
  mkdirSync(snapshot, { mode: 0o700 });
  const archived = await runOwnedProfileCommand(
    [
      gitPath,
      "archive",
      "--format=tar",
      `--output=${archive}`,
      seal.expectedHead,
    ],
    sourceRoot,
    10_000,
  );
  if (
    archived.exitCode !== 0 ||
    archived.timedOut ||
    archived.cancelled ||
    !archived.processReaped ||
    archived.cleanupErrors.length > 0
  )
    throw new Error(`git-archive-failed:${archived.stderr}`);
  const extracted = await runOwnedProfileCommand(
    [tarPath, "-xf", archive, "-C", snapshot],
    temporaryRoot,
    10_000,
  );
  if (
    extracted.exitCode !== 0 ||
    extracted.timedOut ||
    extracted.cancelled ||
    !extracted.processReaped ||
    extracted.cleanupErrors.length > 0
  )
    throw new Error(`archive-extract-failed:${extracted.stderr}`);
  overlayCandidates(snapshot, seal);
  return realpathSync(snapshot);
}

function sandboxProfile(sourceRoot: string, stateRoot: string): string {
  return [
    "(version 1)",
    "(allow default)",
    "(deny network*)",
    `(deny file-write* (subpath ${JSON.stringify(sourceRoot)}))`,
    `(deny file-write* (subpath ${JSON.stringify(stateRoot)}))`,
    "",
  ].join("\n");
}

function isolationCapability(
  allowDegraded: boolean,
  sourceRoot: string,
  stateRoot: string,
) {
  const path = "/usr/bin/sandbox-exec";
  const probe =
    process.platform === "darwin"
      ? spawnSync(
          path,
          ["-p", "(version 1)\n(allow default)\n", "/usr/bin/true"],
          { encoding: "utf8", timeout: 2_000, killSignal: "SIGKILL" },
        )
      : null;
  if (probe?.status === 0) {
    return {
      prefix: [path, "-p", sandboxProfile(sourceRoot, stateRoot)],
      receipt: {
        mode: "darwin-sandbox" as const,
        admission_eligible: false,
        network_denied: true,
        live_repository_write_denied: true,
      },
    };
  }
  if (!allowDegraded)
    throw new Error(
      `darwin-sandbox-unavailable:${probe?.stderr.trim() ?? "unsupported-platform"}`,
    );
  return {
    prefix: [] as string[],
    receipt: {
      mode: "cwd-only-degraded" as const,
      admission_eligible: false,
      network_denied: false,
      live_repository_write_denied: false,
    },
  };
}

function reviewerFinal(stdout: string): ReviewerFinal {
  if (!stdout.endsWith("\n")) throw new Error("reviewer-final-missing-newline");
  const lines = stdout.trimEnd().split(/\r?\n/u);
  if (lines.length !== 1) throw new Error("reviewer-final-line-count-invalid");
  const value = object(JSON.parse(lines[0] ?? ""), "reviewer-final");
  exactKeys(
    value,
    ["schema_version", "status", "summary", "findings"],
    "reviewer-final",
  );
  if (
    value.schema_version !== "gcr-sealed-background-review-stage-final@v1" ||
    (value.status !== "passed" && value.status !== "failed") ||
    typeof value.summary !== "string" ||
    value.summary.length === 0 ||
    !Array.isArray(value.findings)
  )
    throw new Error("reviewer-final-contract-invalid");
  const findings = value.findings.map((raw, index) => {
    const finding = object(raw, `finding-${String(index)}`);
    exactKeys(
      finding,
      ["severity", "code", "summary"],
      `finding-${String(index)}`,
    );
    if (
      !["blocker", "warning", "info"].includes(String(finding.severity)) ||
      typeof finding.code !== "string" ||
      finding.code.length === 0 ||
      typeof finding.summary !== "string" ||
      finding.summary.length === 0
    )
      throw new Error("reviewer-finding-contract-invalid");
    return finding as ReviewerFinal["findings"][number];
  });
  return {
    schema_version: value.schema_version,
    status: value.status,
    summary: value.summary,
    findings,
  };
}

function removeSnapshot(root: string, errors: string[]): boolean {
  const result = spawnSync(requiredExecutable("rm"), ["-rf", "--", root], {
    timeout: SNAPSHOT_CLEANUP_TIMEOUT_MS,
    killSignal: "SIGKILL",
    encoding: "utf8",
  });
  if (result.status === 0 && !result.error && !existsSync(root)) return true;
  const diagnostic =
    result.error?.message ??
    (result.stderr.trim() || `exit-${String(result.status)}`);
  errors.push(`snapshot-cleanup:${diagnostic}`);
  return false;
}

function resultBytes(result: Record<string, unknown>) {
  const bytes = Buffer.from(`${JSON.stringify(result)}\n`);
  return { bytes, digest: sha256(bytes) };
}

function transactionRoot(stateRoot: string, runId: string): string {
  const root = join(stateRoot, "transactions");
  try {
    mkdirSync(root, { mode: 0o700 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  }
  ensureRealDirectory(root, "transaction-root");
  const runRoot = join(root, runId);
  try {
    mkdirSync(runRoot, { mode: 0o700 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  }
  return ensureRealDirectory(runRoot, "transaction-run-root");
}

function prepareResultTransaction(
  stateRoot: string,
  request: AsyncWorkerRequest,
  bytes: Buffer,
): { path: string; parent: WriterParentIdentity } {
  const path = join(
    transactionRoot(stateRoot, request.run_id),
    `${request.fencing_token}.result.json`,
  );
  const parentStats = lstatSync(dirname(path));
  const parent = { dev: parentStats.dev, ino: parentStats.ino };
  publishWriterArtifact(stateRoot, path, bytes, parent);
  if (!readBoundedRegularFile(path, 16 * 1024 * 1024).equals(bytes))
    throw new Error("result-transaction-readback-mismatch");
  return { path, parent };
}

function discardResultTransaction(
  stateRoot: string,
  transaction: { path: string; parent: WriterParentIdentity },
  bytes: Buffer,
): void {
  removeWriterArtifact(stateRoot, transaction.path, bytes, transaction.parent);
}

function pauseResultTransaction(): void {
  if (process.env.REPO_ASYNC_WORKER_PROBE_STAGE !== "result-pre-finish") return;
  process.stderr.write(
    `[async-worker-probe] stage=result-pre-finish pid=${String(process.pid)}\n`,
  );
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 500);
}

function publishCommittedResult(
  stateRoot: string,
  resultRef: string,
  bytes: Buffer,
): void {
  let lastFailure: unknown;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    try {
      const output = join(stateRoot, resultRef);
      publishWriterArtifact(stateRoot, output, bytes);
      const reopened = readBoundedRegularFile(output, 16 * 1024 * 1024);
      if (!reopened.equals(bytes))
        throw new Error("worker-result-readback-mismatch");
      return;
    } catch (error) {
      lastFailure = error;
    }
  }
  throw new Error("committed-result-publication-exhausted", {
    cause: lastFailure,
  });
}

function existingResultBytes(path: string): Buffer | undefined {
  try {
    return readBoundedRegularFile(path, 16 * 1024 * 1024);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
    throw error;
  }
}

function completionFromCommittedBytes(
  request: AsyncWorkerRequest,
  job: AsyncWorkerJob,
  status: "verified" | "failed" | "stale",
  digest: string,
  bytes: Buffer,
): AsyncWorkerCompletion {
  const value = object(JSON.parse(bytes.toString("utf8")), "worker-result");
  if (
    value.schema_version !== "repo-async-production-worker-result@v1" ||
    value.status !== status ||
    value.run_id !== request.run_id ||
    value.result_ref !== job.result_ref
  )
    throw new Error("committed-result-recovery-contract-mismatch");
  const cleanup = object(value.cleanup, "worker-result-cleanup");
  const processReaped = cleanup.process_reaped;
  const timerCleared = cleanup.timer_cleared;
  const snapshotRemoved = cleanup.snapshot_removed;
  if (
    typeof processReaped !== "boolean" ||
    typeof timerCleared !== "boolean" ||
    typeof snapshotRemoved !== "boolean"
  )
    throw new Error("committed-result-recovery-cleanup-invalid");
  const diagnostic =
    typeof value.diagnostic === "string" && value.diagnostic.length > 0
      ? value.diagnostic
      : undefined;
  return {
    schema_version: "repo-async-production-worker-completion@v1",
    status,
    run_id: request.run_id,
    result_ref: job.result_ref,
    result_sha256: digest,
    process_reaped: processReaped,
    timer_cleared: timerCleared,
    snapshot_removed: snapshotRemoved,
    ...(diagnostic === undefined ? {} : { diagnostic }),
  };
}

function recoverCommittedResult(
  stateRoot: string,
  request: AsyncWorkerRequest,
  job: AsyncWorkerJob,
): AsyncWorkerCompletion | undefined {
  const current = inspectAsyncProductionRun(stateRoot, request.run_id);
  if (
    current.status !== "verified" &&
    current.status !== "failed" &&
    current.status !== "stale"
  )
    return undefined;
  if (
    current.version !== request.expected_version + 2 ||
    current.fencingToken !== request.fencing_token ||
    current.resultRef !== job.result_ref ||
    current.resultSha256 === undefined
  )
    throw new Error("committed-result-recovery-binding-mismatch");

  const output = join(stateRoot, job.result_ref);
  const path = join(
    stateRoot,
    "transactions",
    request.run_id,
    `${request.fencing_token}.result.json`,
  );
  let bytes = existingResultBytes(output);
  if (bytes === undefined) {
    bytes = existingResultBytes(path);
    if (bytes === undefined)
      throw new Error("committed-result-recovery-source-missing");
    if (sha256(bytes) !== current.resultSha256)
      throw new Error("committed-result-recovery-hash-mismatch");
    publishCommittedResult(stateRoot, job.result_ref, bytes);
  }
  if (sha256(bytes) !== current.resultSha256)
    throw new Error("committed-result-public-hash-mismatch");
  if (
    current.transactionParentDev === undefined ||
    current.transactionParentIno === undefined
  )
    throw new Error("committed-result-recovery-parent-binding-missing");
  discardResultTransaction(
    stateRoot,
    {
      path,
      parent: {
        dev: current.transactionParentDev,
        ino: current.transactionParentIno,
      },
    },
    bytes,
  );
  return completionFromCommittedBytes(
    request,
    job,
    current.status,
    current.resultSha256,
    bytes,
  );
}

function commitResultTransaction(
  stateRoot: string,
  request: AsyncWorkerRequest,
  job: AsyncWorkerJob,
  terminalStatus: "verified" | "failed" | "stale",
  immutable: { bytes: Buffer; digest: string },
): { status: "committed" } | { status: "cancelled"; diagnostic: string } {
  const pending = prepareResultTransaction(stateRoot, request, immutable.bytes);
  pauseResultTransaction();
  try {
    finishAsyncProductionRun(stateRoot, {
      runId: request.run_id,
      expectedVersion: request.expected_version + 1,
      fencingToken: request.fencing_token,
      now: new Date(),
      terminalStatus,
      resultRef: job.result_ref,
      resultSha256: immutable.digest,
      transactionParentDev: pending.parent.dev,
      transactionParentIno: pending.parent.ino,
    });
  } catch (error) {
    const current = inspectAsyncProductionRun(stateRoot, request.run_id);
    discardResultTransaction(stateRoot, pending, immutable.bytes);
    if (current.status === "cancelled")
      return {
        status: "cancelled",
        diagnostic:
          current.diagnostic ?? "foreground cancellation won finish CAS",
      };
    throw error;
  }
  if (process.env.REPO_ASYNC_WORKER_FAIL_STAGE === "after-finish")
    throw new Error("injected-after-finish-failure");
  publishCommittedResult(stateRoot, job.result_ref, immutable.bytes);
  discardResultTransaction(stateRoot, pending, immutable.bytes);
  return { status: "committed" };
}

function cancelledCompletion(
  request: AsyncWorkerRequest,
  snapshotRemoved: boolean,
  diagnostic: string,
  cleanup: { processReaped: boolean; timerCleared: boolean },
): AsyncWorkerCompletion {
  return {
    schema_version: "repo-async-production-worker-completion@v1",
    status: "cancelled",
    run_id: request.run_id,
    process_reaped: cleanup.processReaped,
    timer_cleared: cleanup.timerCleared,
    snapshot_removed: snapshotRemoved,
    diagnostic,
  };
}

export async function executeAsyncWorker(
  request: AsyncWorkerRequest,
  stateRootInput: string,
  sourceRootInput: string,
  emitProgress: AsyncWorkerProgressSink = () => {},
): Promise<AsyncWorkerCompletion> {
  const started = Date.now();
  const stateRoot = ensureRealDirectory(stateRootInput, "state-root");
  const sourceRoot = ensureRealDirectory(sourceRootInput, "source-root");
  ensureRealDirectory(join(stateRoot, "runs"), "runs-root");
  ensureRealDirectory(join(stateRoot, "results"), "results-root");
  const seal = openAsyncProductionSeal(
    stateRoot,
    request.run_id,
    request.expected_seal_sha256,
  );
  const job = parseJob(seal);
  if (request.lease_ms < job.timeout_ms + MIN_LEASE_FINISH_BUDGET_MS)
    throw new Error("lease-must-cover-timeout-and-finish-budget");
  const executable = executableIdentity(
    job.command[0] as string,
    job.executable_sha256,
  );
  const recovered = recoverCommittedResult(stateRoot, request, job);
  if (recovered) {
    progress(emitProgress, request, "finish", "started");
    progress(
      emitProgress,
      request,
      "finish",
      "finished",
      recovered.status === "verified" ? "passed" : recovered.status,
    );
    return recovered;
  }
  const temporaryRoot = mkdtempSync(
    join(tmpdir(), `${request.run_id}-sealed-worker-`),
  );
  let snapshotRemoved = false;
  let claimed = false;
  let isolation: ReturnType<typeof isolationCapability> | undefined;
  let commandResult: OwnedProfileCommandResult | undefined;
  let watcherReleased = false;
  let watcherError: unknown;
  const cleanupErrors: string[] = [];
  try {
    progress(emitProgress, request, "materialize", "started");
    const snapshot = await materializeSnapshot(sourceRoot, seal, temporaryRoot);
    progress(emitProgress, request, "materialize", "finished", "passed");
    const target = ensureRealDirectory(
      join(snapshot, seal.targetRepo),
      "sealed-target-repo",
    );
    const snapshotLocal = relative(snapshot, target);
    if (snapshotLocal.startsWith(`..${sep}`) || isAbsolute(snapshotLocal))
      throw new Error("target-repo-escaped-snapshot");
    const driver = driverIdentity(target, job);
    isolation = isolationCapability(job.allow_degraded, sourceRoot, stateRoot);
    const claimTime = new Date();
    progress(emitProgress, request, "claim", "started");
    claimAsyncProductionRun(stateRoot, {
      runId: request.run_id,
      expectedVersion: request.expected_version,
      expectedSealSha256: request.expected_seal_sha256,
      workerId: request.worker_id,
      now: claimTime,
      leaseExpiresAt: new Date(claimTime.getTime() + request.lease_ms),
      fencingToken: request.fencing_token,
    });
    claimed = true;
    progress(emitProgress, request, "claim", "finished", "passed");
    if (process.env.REPO_ASYNC_WORKER_FAIL_STAGE === "after-claim")
      throw new Error("injected-after-claim-failure");
    const abort = new AbortController();
    const watcher = setInterval(() => {
      try {
        if (
          process.env.REPO_ASYNC_WORKER_FAIL_STAGE === "lifecycle-watch" &&
          watcherError === undefined
        )
          throw new Error("injected-lifecycle-watch-failure");
        const current = inspectAsyncProductionRun(stateRoot, request.run_id);
        if (
          current.status !== "running-production" ||
          current.version !== request.expected_version + 1 ||
          current.fencingToken !== request.fencing_token
        )
          abort.abort();
      } catch (error) {
        watcherError ??= error;
        abort.abort();
      }
    }, 20);
    progress(emitProgress, request, "review", "started");
    try {
      commandResult = await runOwnedProfileCommand(
        [...isolation.prefix, executable.real_path, ...job.command.slice(1)],
        target,
        job.timeout_ms,
        { signal: abort.signal },
      );
    } finally {
      clearInterval(watcher);
      watcherReleased = true;
    }
    if (watcherError !== undefined)
      throw new Error("lifecycle-watch-failed", { cause: watcherError });
    const current = inspectAsyncProductionRun(stateRoot, request.run_id);
    snapshotRemoved = removeSnapshot(temporaryRoot, cleanupErrors);
    if (
      current.status === "cancelled" ||
      commandResult.cancelled ||
      current.version !== request.expected_version + 1
    ) {
      progress(emitProgress, request, "review", "finished", "cancelled");
      return cancelledCompletion(
        request,
        snapshotRemoved,
        current.diagnostic ?? "worker claim was cancelled or superseded",
        {
          processReaped: commandResult.processReaped,
          timerCleared: commandResult.timerCleared && watcherReleased,
        },
      );
    }

    const currentHead = gitHead(requiredExecutable("git"), sourceRoot);
    let final: ReviewerFinal | undefined;
    let diagnostic = commandResult.timedOut ? "reviewer timed out" : "";
    if (!commandResult.timedOut) {
      try {
        final = reviewerFinal(commandResult.stdout);
      } catch (error) {
        diagnostic = error instanceof Error ? error.message : String(error);
      }
    }
    cleanupErrors.push(...commandResult.cleanupErrors);
    const clean =
      commandResult.processReaped &&
      commandResult.timerCleared &&
      commandResult.stdoutConsumed &&
      commandResult.stderrConsumed &&
      cleanupErrors.length === 0 &&
      snapshotRemoved &&
      watcherReleased;
    const stale = currentHead !== seal.expectedHead;
    const verified =
      !stale &&
      commandResult.exitCode === 0 &&
      !commandResult.timedOut &&
      !commandResult.cancelled &&
      final?.status === "passed" &&
      clean;
    const status: "verified" | "failed" | "stale" = stale
      ? "stale"
      : verified
        ? "verified"
        : "failed";
    progress(
      emitProgress,
      request,
      "review",
      "finished",
      status === "verified" ? "passed" : status,
    );
    if (!diagnostic && status === "failed") {
      diagnostic =
        cleanupErrors.join("|") ||
        commandResult.stderr.trim() ||
        `reviewer failed with exit ${String(commandResult.exitCode)}`;
    }
    const immutable = resultBytes({
      schema_version: "repo-async-production-worker-result@v1",
      status,
      run_id: request.run_id,
      seal_sha256: seal.sealSha256,
      candidate_sha256: seal.candidateSha256,
      production_job_sha256: seal.productionJobSha256,
      result_ref: job.result_ref,
      source: {
        expected_head: seal.expectedHead,
        current_head: currentHead,
        binding: stale ? "stale" : "current",
      },
      command: { argv: job.command, executable, driver },
      ...(final ? { reviewer_final: final } : {}),
      ...(diagnostic ? { diagnostic } : {}),
      isolation: isolation.receipt,
      cleanup: {
        process_reaped: commandResult.processReaped,
        timer_cleared: commandResult.timerCleared && watcherReleased,
        stdout_consumed: commandResult.stdoutConsumed,
        stderr_consumed: commandResult.stderrConsumed,
        snapshot_removed: snapshotRemoved,
        cleanup_errors: cleanupErrors,
      },
      elapsed_ms: Math.max(0, Date.now() - started),
    });
    progress(emitProgress, request, "finish", "started");
    const transaction = commitResultTransaction(
      stateRoot,
      request,
      job,
      status,
      immutable,
    );
    if (transaction.status === "cancelled") {
      progress(emitProgress, request, "finish", "finished", "cancelled");
      return cancelledCompletion(
        request,
        snapshotRemoved,
        transaction.diagnostic,
        {
          processReaped: commandResult.processReaped,
          timerCleared: commandResult.timerCleared && watcherReleased,
        },
      );
    }
    progress(
      emitProgress,
      request,
      "finish",
      "finished",
      status === "verified" ? "passed" : status,
    );
    return {
      schema_version: "repo-async-production-worker-completion@v1",
      status,
      run_id: request.run_id,
      result_ref: job.result_ref,
      result_sha256: immutable.digest,
      process_reaped: commandResult.processReaped,
      timer_cleared: commandResult.timerCleared && watcherReleased,
      snapshot_removed: snapshotRemoved,
      ...(diagnostic ? { diagnostic } : {}),
    };
  } catch (error) {
    if (!snapshotRemoved)
      snapshotRemoved = removeSnapshot(temporaryRoot, cleanupErrors);
    if (!claimed) throw error;
    if (!isolation)
      throw new AggregateError(
        [error],
        "claimed worker lost its preflight isolation receipt",
        { cause: error },
      );
    const current = inspectAsyncProductionRun(stateRoot, request.run_id);
    if (current.status === "cancelled")
      return cancelledCompletion(
        request,
        snapshotRemoved,
        current.diagnostic ?? diagnosticChain(error),
        {
          processReaped: commandResult?.processReaped ?? true,
          timerCleared:
            (commandResult?.timerCleared ?? true) && watcherReleased,
        },
      );
    if (
      current.status !== "running-production" ||
      current.version !== request.expected_version + 1 ||
      current.fencingToken !== request.fencing_token
    )
      throw error;
    const source = reopenedSourceBinding(sourceRoot, seal.expectedHead);
    const status = source.binding === "stale" ? "stale" : "failed";
    const allCleanupErrors = [
      ...new Set([...cleanupErrors, ...(commandResult?.cleanupErrors ?? [])]),
    ];
    const diagnostic = diagnosticChain(error);
    const immutable = resultBytes({
      schema_version: "repo-async-production-worker-result@v1",
      status,
      run_id: request.run_id,
      seal_sha256: seal.sealSha256,
      candidate_sha256: seal.candidateSha256,
      production_job_sha256: seal.productionJobSha256,
      result_ref: job.result_ref,
      source,
      command: {
        argv: job.command,
        executable,
        driver: { path: job.driver_path, sha256: job.driver_sha256 },
      },
      diagnostic,
      isolation: isolation.receipt,
      cleanup: {
        process_reaped: commandResult?.processReaped ?? true,
        timer_cleared: (commandResult?.timerCleared ?? true) && watcherReleased,
        stdout_consumed: commandResult?.stdoutConsumed ?? true,
        stderr_consumed: commandResult?.stderrConsumed ?? true,
        snapshot_removed: snapshotRemoved,
        cleanup_errors: allCleanupErrors,
      },
      elapsed_ms: Math.max(0, Date.now() - started),
    });
    progress(emitProgress, request, "finish", "started");
    const transaction = commitResultTransaction(
      stateRoot,
      request,
      job,
      status,
      immutable,
    );
    if (transaction.status === "cancelled") {
      progress(emitProgress, request, "finish", "finished", "cancelled");
      return cancelledCompletion(
        request,
        snapshotRemoved,
        transaction.diagnostic,
        {
          processReaped: commandResult?.processReaped ?? true,
          timerCleared:
            (commandResult?.timerCleared ?? true) && watcherReleased,
        },
      );
    }
    progress(
      emitProgress,
      request,
      "finish",
      "finished",
      status === "stale" ? "stale" : "failed",
    );
    return {
      schema_version: "repo-async-production-worker-completion@v1",
      status,
      run_id: request.run_id,
      result_ref: job.result_ref,
      result_sha256: immutable.digest,
      process_reaped: commandResult?.processReaped ?? true,
      timer_cleared: (commandResult?.timerCleared ?? true) && watcherReleased,
      snapshot_removed: snapshotRemoved,
      diagnostic,
    };
  } finally {
    if (!snapshotRemoved) removeSnapshot(temporaryRoot, cleanupErrors);
  }
}

export function readAsyncWorkerRequest(path: string): AsyncWorkerRequest {
  return parseAsyncWorkerRequest(
    readBoundedRegularFile(path, MAX_REQUEST_BYTES),
  );
}
