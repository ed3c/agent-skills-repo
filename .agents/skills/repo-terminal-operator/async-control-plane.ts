import { createHash } from "node:crypto";
import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  readSync,
  readdirSync,
} from "node:fs";
import { join } from "node:path";
import {
  inspectAsyncProductionRun,
  openAsyncProductionSeal,
  type AsyncProductionView,
} from "./async-job-lifecycle";
import {
  minimumAsyncWorkerLeaseMs,
  type AsyncWorkerProgress,
  type AsyncWorkerRequest,
} from "./async-worker-carrier";
import { publishWriterArtifact } from "./writer-publication";

const IDENTIFIER = /^[a-z0-9][a-z0-9._-]{0,127}$/u;
const MAX_REQUEST_BYTES = 64 * 1024;
const MAX_RESULT_BYTES = 16 * 1024 * 1024;

export type AsyncControlRequest = {
  schema_version: "repo-async-production-control-request@v1";
  projection_id: string;
  mode: "project-only";
  run_ids: string[];
  observed_at: string;
  worker_lease_ms: number;
  max_redrives: number;
  sweep_orphans: boolean;
};

type RunDisposition =
  | "dispatch"
  | "await"
  | "recover"
  | "foreground-admission"
  | "repair-or-close"
  | "closed";

type ProjectedRun = {
  run_id: string;
  lifecycle_status: AsyncProductionView["status"];
  disposition: RunDisposition;
  lifecycle_version: number;
  seal_sha256: string;
  worker_request_ref?: string;
  worker_request_sha256?: string;
  progress?: {
    fencing_token: string;
    last_sequence: number;
    stage: AsyncWorkerProgress["stage"];
    state: AsyncWorkerProgress["state"];
    status?: AsyncWorkerProgress["status"];
    event_ref: string;
    event_sha256: string;
  };
};

export type AsyncControlCompletion = {
  schema_version: "repo-async-production-control-completion@v1";
  status: "projected";
  projection_id: string;
  projection_ref: string;
  projection_sha256: string;
  admission_eligible: false;
};

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
  if (
    JSON.stringify(Object.keys(value).sort()) !==
    JSON.stringify([...expected].sort())
  )
    throw new Error(`${label}-fields-invalid`);
}

function boundedInteger(
  value: unknown,
  label: string,
  minimum: number,
  maximum: number,
): number {
  if (
    !Number.isSafeInteger(value) ||
    Number(value) < minimum ||
    Number(value) > maximum
  )
    throw new Error(`${label}-invalid`);
  return Number(value);
}

function ensureRealDirectory(root: string, name: string): string {
  const path = join(root, name);
  try {
    mkdirSync(path, { mode: 0o700 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
  }
  const stat = lstatSync(path);
  if (!stat.isDirectory() || stat.isSymbolicLink())
    throw new Error(`${name}-must-be-real-directory`);
  return path;
}

function readBoundedRegularFile(
  path: string,
  maximumBytes: number,
  label: string,
): Buffer {
  const descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = fstatSync(descriptor);
    if (!stat.isFile() || stat.size > maximumBytes)
      throw new Error(`${label}-not-bounded-regular-file`);
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
    if (offset !== stat.size) throw new Error(`${label}-changed-during-read`);
    return snapshot.subarray(0, offset);
  } finally {
    closeSync(descriptor);
  }
}

export function readAsyncControlRequest(path: string): Buffer {
  return readBoundedRegularFile(path, MAX_REQUEST_BYTES, "control-request");
}

export function parseAsyncControlRequest(
  bytes: Uint8Array,
): AsyncControlRequest {
  if (bytes.byteLength > MAX_REQUEST_BYTES)
    throw new Error("control-request-too-large");
  const input = object(
    JSON.parse(Buffer.from(bytes).toString("utf8")),
    "control-request",
  );
  exactKeys(
    input,
    [
      "schema_version",
      "projection_id",
      "mode",
      "run_ids",
      "observed_at",
      "worker_lease_ms",
      "max_redrives",
      "sweep_orphans",
    ],
    "control-request",
  );
  if (
    input.schema_version !== "repo-async-production-control-request@v1" ||
    typeof input.projection_id !== "string" ||
    !IDENTIFIER.test(input.projection_id) ||
    input.mode !== "project-only" ||
    !Array.isArray(input.run_ids) ||
    input.run_ids.length === 0 ||
    input.run_ids.length > 256 ||
    !input.run_ids.every(
      (runId) => typeof runId === "string" && IDENTIFIER.test(runId),
    ) ||
    new Set(input.run_ids).size !== input.run_ids.length ||
    typeof input.observed_at !== "string" ||
    new Date(input.observed_at).toISOString() !== input.observed_at ||
    typeof input.sweep_orphans !== "boolean"
  )
    throw new Error("control-request-contract-invalid");
  return {
    schema_version: input.schema_version,
    projection_id: input.projection_id,
    mode: input.mode,
    run_ids: [...(input.run_ids as string[])].sort(),
    observed_at: input.observed_at,
    worker_lease_ms: boundedInteger(
      input.worker_lease_ms,
      "worker-lease-ms",
      100,
      3_600_000,
    ),
    max_redrives: boundedInteger(input.max_redrives, "max-redrives", 1, 3),
    sweep_orphans: input.sweep_orphans,
  };
}

function existingBoundArtifact(
  path: string,
  expectedSha256: string,
  label: string,
): "missing" | "valid" {
  let descriptor: number | undefined;
  try {
    descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
    const stat = fstatSync(descriptor);
    if (!stat.isFile() || stat.size > MAX_RESULT_BYTES)
      throw new Error(`${label}-not-bounded-regular-file`);
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
    if (offset !== stat.size) throw new Error(`${label}-changed-during-read`);
    if (sha256(snapshot.subarray(0, offset)) !== expectedSha256)
      throw new Error(`${label}-hash-mismatch`);
    return "valid";
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return "missing";
    throw error;
  } finally {
    if (descriptor !== undefined) closeSync(descriptor);
  }
}

function terminalDisposition(
  stateRoot: string,
  view: AsyncProductionView,
): RunDisposition {
  if (!view.resultRef || !view.resultSha256) return "repair-or-close";
  if (
    existingBoundArtifact(
      join(stateRoot, view.resultRef),
      view.resultSha256,
      "public-result",
    ) === "valid"
  )
    return "foreground-admission";
  if (
    !view.fencingToken ||
    view.transactionParentDev === undefined ||
    view.transactionParentIno === undefined
  )
    return "repair-or-close";
  const transactions = ensureRealDirectory(stateRoot, "transactions");
  const runTransactions = ensureRealDirectory(transactions, view.runId);
  const parent = lstatSync(runTransactions);
  if (
    parent.dev !== view.transactionParentDev ||
    parent.ino !== view.transactionParentIno
  )
    throw new Error(`transaction-parent-binding-mismatch:${view.runId}`);
  const pending = join(runTransactions, `${view.fencingToken}.result.json`);
  return existingBoundArtifact(pending, view.resultSha256, "pending-result") ===
    "valid"
    ? "recover"
    : "repair-or-close";
}

function classify(
  stateRoot: string,
  view: AsyncProductionView,
  now: Date,
): RunDisposition {
  if (
    view.status === "verified" ||
    view.status === "failed" ||
    view.status === "stale"
  )
    return terminalDisposition(stateRoot, view);
  if (view.status === "cancelled") return "closed";
  if (now.getTime() >= Date.parse(view.deadlineAt)) return "repair-or-close";
  if (
    view.status === "awaiting-production" ||
    view.nextAction === "verification-lease-expired-reclaimable"
  )
    return "dispatch";
  if (view.status === "running-production") return "await";
  return "repair-or-close";
}

function publishWorkerRequest(
  stateRoot: string,
  workerRequest: AsyncWorkerRequest,
  ref: string,
): Pick<ProjectedRun, "worker_request_ref" | "worker_request_sha256"> {
  const bytes = Buffer.from(`${JSON.stringify(workerRequest)}\n`);
  const output = join(stateRoot, ref);
  publishWriterArtifact(stateRoot, output, bytes);
  const reopened = readFileSync(output);
  if (!reopened.equals(bytes))
    throw new Error("queue-request-readback-mismatch");
  return { worker_request_ref: ref, worker_request_sha256: sha256(reopened) };
}

function projectDispatch(
  stateRoot: string,
  view: AsyncProductionView,
  request: AsyncControlRequest,
  now: Date,
): Pick<ProjectedRun, "worker_request_ref" | "worker_request_sha256"> {
  const seal = openAsyncProductionSeal(stateRoot, view.runId, view.sealSha256);
  const minimumLease = minimumAsyncWorkerLeaseMs(seal);
  const remaining = Date.parse(view.deadlineAt) - now.getTime();
  if (request.worker_lease_ms < minimumLease)
    throw new Error(`worker-lease-insufficient:${view.runId}`);
  if (request.worker_lease_ms > remaining)
    throw new Error(`worker-lease-exceeds-run-deadline:${view.runId}`);
  const identity = sha256(`${view.sealSha256}:${view.version}`);
  return publishWorkerRequest(
    stateRoot,
    {
      schema_version: "repo-async-production-worker-request@v1",
      run_id: view.runId,
      expected_version: view.version,
      expected_seal_sha256: view.sealSha256,
      worker_id: `redrive-${identity.slice(0, 16)}`,
      fencing_token: `fence-${identity.slice(0, 24)}`,
      lease_ms: request.worker_lease_ms,
    },
    `queue/${view.runId}.v${view.version}.json`,
  );
}

function projectRecovery(
  stateRoot: string,
  view: AsyncProductionView,
  request: AsyncControlRequest,
): Pick<ProjectedRun, "worker_request_ref" | "worker_request_sha256"> {
  const seal = openAsyncProductionSeal(stateRoot, view.runId, view.sealSha256);
  if (request.worker_lease_ms < minimumAsyncWorkerLeaseMs(seal))
    throw new Error(`worker-lease-insufficient:${view.runId}`);
  if (view.version < 2 || !view.fencingToken)
    throw new Error(`terminal-recovery-binding-missing:${view.runId}`);
  const expectedVersion = view.version - 2;
  const identity = sha256(
    `${view.sealSha256}:${expectedVersion}:${view.fencingToken}:recover`,
  );
  return publishWorkerRequest(
    stateRoot,
    {
      schema_version: "repo-async-production-worker-request@v1",
      run_id: view.runId,
      expected_version: expectedVersion,
      expected_seal_sha256: view.sealSha256,
      worker_id: `recover-${identity.slice(0, 16)}`,
      fencing_token: view.fencingToken,
      lease_ms: request.worker_lease_ms,
    },
    `queue/${view.runId}.recover.v${view.version}.json`,
  );
}

function classifyOrphans(
  stateRoot: string,
  views: AsyncProductionView[],
  runs: ProjectedRun[],
  enabled: boolean,
): string[] {
  if (!enabled) return [];
  const transactions = ensureRealDirectory(stateRoot, "transactions");
  const expected = new Set(
    views
      .filter(
        (view, index) =>
          runs[index]?.disposition === "recover" &&
          view.fencingToken !== undefined &&
          view.transactionParentDev !== undefined &&
          view.transactionParentIno !== undefined,
      )
      .map(
        (view) => `transactions/${view.runId}/${view.fencingToken}.result.json`,
      ),
  );
  return views
    .flatMap((view) => {
      const runTransactions = ensureRealDirectory(transactions, view.runId);
      return readdirSync(runTransactions, { withFileTypes: true })
        .filter((entry) => entry.name.endsWith(".result.json"))
        .map((entry) => {
          if (!entry.isFile())
            throw new Error(
              `orphan-transaction-not-regular-file:${view.runId}/${entry.name}`,
            );
          const bytes = readBoundedRegularFile(
            join(runTransactions, entry.name),
            MAX_RESULT_BYTES,
            "orphan-transaction",
          );
          let value: unknown;
          try {
            value = JSON.parse(bytes.toString("utf8"));
          } catch (error) {
            throw new Error(
              `orphan-transaction-json-invalid:${view.runId}/${entry.name}`,
              { cause: error },
            );
          }
          if (!value || typeof value !== "object" || Array.isArray(value))
            throw new Error(
              `orphan-transaction-contract-invalid:${view.runId}/${entry.name}`,
            );
          if ((value as Record<string, unknown>).run_id !== view.runId)
            throw new Error(
              `orphan-transaction-run-binding-invalid:${view.runId}/${entry.name}`,
            );
          return `transactions/${view.runId}/${entry.name}`;
        });
    })
    .filter((ref) => !expected.has(ref))
    .sort()
    .map((ref) => ref);
}

function parseProgress(
  bytes: Buffer,
  runId: string,
  name: string,
): AsyncWorkerProgress {
  if (bytes.byteLength > MAX_REQUEST_BYTES)
    throw new Error(`progress-record-too-large:${name}`);
  const input = object(JSON.parse(bytes.toString("utf8")), "progress-record");
  const allowed = [
    "schema_version",
    "run_id",
    "expected_version",
    "seal_sha256",
    "fencing_token",
    "sequence",
    "stage",
    "state",
    "admission_eligible",
    ...(input.status === undefined ? [] : ["status"]),
  ];
  exactKeys(input, allowed, "progress-record");
  const sequence = boundedInteger(input.sequence, "progress-sequence", 1, 8);
  const stage = input.stage;
  const state = input.state;
  const expectedSequence =
    ["materialize", "claim", "review", "finish"].indexOf(String(stage)) * 2 +
    (state === "started" ? 1 : 2);
  if (
    input.schema_version !== "repo-async-production-worker-progress@v2" ||
    typeof input.run_id !== "string" ||
    !IDENTIFIER.test(input.run_id) ||
    typeof input.seal_sha256 !== "string" ||
    !/^[a-f0-9]{64}$/u.test(input.seal_sha256) ||
    typeof input.fencing_token !== "string" ||
    !IDENTIFIER.test(input.fencing_token) ||
    !["materialize", "claim", "review", "finish"].includes(String(stage)) ||
    !["started", "finished"].includes(String(state)) ||
    sequence !== expectedSequence ||
    input.admission_eligible !== false ||
    (state === "started"
      ? input.status !== undefined
      : state === "finished" && input.status === undefined) ||
    (input.status !== undefined &&
      !["passed", "failed", "stale", "cancelled"].includes(
        String(input.status),
      ))
  )
    throw new Error(`progress-record-contract-invalid:${name}`);
  const event: AsyncWorkerProgress = {
    schema_version: input.schema_version,
    run_id: input.run_id,
    expected_version: boundedInteger(
      input.expected_version,
      "progress-expected-version",
      0,
      Number.MAX_SAFE_INTEGER,
    ),
    seal_sha256: input.seal_sha256,
    fencing_token: input.fencing_token,
    sequence,
    stage: stage as AsyncWorkerProgress["stage"],
    state: state as AsyncWorkerProgress["state"],
    ...(typeof input.status === "string"
      ? {
          status: input.status as NonNullable<AsyncWorkerProgress["status"]>,
        }
      : {}),
    admission_eligible: false,
  };
  if (
    event.run_id !== runId ||
    name !==
      `${event.fencing_token}.${String(event.sequence).padStart(2, "0")}.json`
  )
    throw new Error(`progress-record-name-mismatch:${name}`);
  return event;
}

type ProgressRecord = {
  name: string;
  bytes: Buffer;
  event: AsyncWorkerProgress;
};

function loadProgressRecords(
  stateRoot: string,
  views: AsyncProductionView[],
): ProgressRecord[] {
  const root = ensureRealDirectory(stateRoot, "progress");
  return views.flatMap((view) => {
    const runRoot = ensureRealDirectory(root, view.runId);
    return readdirSync(runRoot, { withFileTypes: true })
      .filter((entry) => entry.name.endsWith(".json"))
      .map((entry) => {
        if (!entry.isFile())
          throw new Error(`progress-record-not-regular-file:${entry.name}`);
        const bytes = readAsyncControlRequest(join(runRoot, entry.name));
        return {
          name: entry.name,
          bytes,
          event: parseProgress(bytes, view.runId, entry.name),
        };
      });
  });
}

function progressProjection(
  view: AsyncProductionView,
  records: ProgressRecord[],
): ProjectedRun["progress"] {
  const bound = records.filter(({ event }) => {
    if (event.run_id !== view.runId || event.seal_sha256 !== view.sealSha256)
      return false;
    return view.fencingToken ? event.fencing_token === view.fencingToken : true;
  });
  const expectedVersions =
    view.status === "running-production"
      ? [view.version - 1]
      : ["verified", "failed", "stale"].includes(view.status)
        ? [view.version - 2]
        : view.status === "cancelled"
          ? [view.version - 2, view.version - 1].filter(
              (version) => version >= 0,
            )
          : [view.version];
  const expectedVersion = expectedVersions.find((version) =>
    bound.some(({ event }) => event.expected_version === version),
  );
  if (bound.length > 0 && expectedVersion === undefined)
    throw new Error(`progress-expected-version-invalid:${view.runId}`);
  const candidates = bound
    .filter(({ event }) => event.expected_version === expectedVersion)
    .sort((left, right) => left.event.sequence - right.event.sequence);
  if (candidates.length === 0) return undefined;
  const fence = candidates[0]?.event.fencing_token;
  const firstSequence = candidates[0]?.event.sequence;
  const terminalRecoverySequence =
    ["verified", "failed", "stale"].includes(view.status) &&
    firstSequence === 7;
  const allowedTransitions = new Set([
    "1:2",
    "2:3",
    "3:4",
    "4:5",
    "4:7",
    "5:6",
    "5:7",
    "6:7",
    "7:8",
  ]);
  if (
    !fence ||
    firstSequence === undefined ||
    (firstSequence !== 1 && !terminalRecoverySequence) ||
    candidates.some((candidate) => candidate.event.fencing_token !== fence) ||
    candidates.some(
      (candidate, index) =>
        index > 0 &&
        !allowedTransitions.has(
          `${String(candidates[index - 1]?.event.sequence)}:${String(candidate.event.sequence)}`,
        ),
    )
  )
    throw new Error(`progress-sequence-or-fence-invalid:${view.runId}`);
  const latest = candidates.at(-1);
  if (!latest) return undefined;
  return {
    fencing_token: latest.event.fencing_token,
    last_sequence: latest.event.sequence,
    stage: latest.event.stage,
    state: latest.event.state,
    ...(latest.event.status === undefined
      ? {}
      : { status: latest.event.status }),
    event_ref: `progress/${view.runId}/${latest.name}`,
    event_sha256: sha256(latest.bytes),
  };
}

export function projectAsyncControl(
  stateRoot: string,
  request: AsyncControlRequest,
): AsyncControlCompletion {
  const rootStat = lstatSync(stateRoot);
  if (!rootStat.isDirectory() || rootStat.isSymbolicLink())
    throw new Error("state-root-must-be-real-directory");
  ensureRealDirectory(stateRoot, "runs");
  ensureRealDirectory(stateRoot, "queue");
  ensureRealDirectory(stateRoot, "projections");
  const now = new Date(request.observed_at);
  const views = request.run_ids.map((runId) =>
    inspectAsyncProductionRun(stateRoot, runId, now),
  );
  const progressRecords = loadProgressRecords(stateRoot, views);
  const runs: ProjectedRun[] = views.map((view) => {
    const disposition = classify(stateRoot, view, now);
    const progress = progressProjection(view, progressRecords);
    return {
      run_id: view.runId,
      lifecycle_status: view.status,
      disposition,
      lifecycle_version: view.version,
      seal_sha256: view.sealSha256,
      ...(disposition === "dispatch"
        ? projectDispatch(stateRoot, view, request, now)
        : disposition === "recover"
          ? projectRecovery(stateRoot, view, request)
          : {}),
      ...(progress === undefined ? {} : { progress }),
    };
  });
  const orphans = classifyOrphans(
    stateRoot,
    views,
    runs,
    request.sweep_orphans,
  );
  const counts = {
    total: runs.length,
    dispatch: runs.filter((run) => run.disposition === "dispatch").length,
    recover: runs.filter((run) => run.disposition === "recover").length,
    orphan: orphans.length,
  };
  const projection = {
    schema_version: "repo-async-production-control-projection@v1",
    status: "projected",
    projection_id: request.projection_id,
    observed_at: request.observed_at,
    counts,
    runs,
    orphans,
    policy: {
      max_redrives: request.max_redrives,
      sweep_orphans: request.sweep_orphans,
      execution: "external-explicit-dispatch-only",
    },
    next_prompt:
      "Intent-Slice: HARNESS-CROSS-CUTTING-ASYNC-PRODUCTION-REDRIVE-PROGRESS-PROJECTOR; " +
      `dispatch=${counts.dispatch}, recover=${counts.recover}, orphan=${counts.orphan}. ` +
      "Consume explicit queue refs only after the bounded worker gate is enabled; keep admission foreground-only.",
    activation: {
      workers_executed: false,
      background_admission_enabled: false,
      forgejo_enabled: false,
      cloud_enabled: false,
    },
    admission_eligible: false,
  } as const;
  const bytes = Buffer.from(`${JSON.stringify(projection)}\n`);
  const projectionRef = `projections/${request.projection_id}.json`;
  const output = join(stateRoot, projectionRef);
  publishWriterArtifact(stateRoot, output, bytes);
  const reopened = readFileSync(output);
  if (!reopened.equals(bytes)) throw new Error("projection-readback-mismatch");
  return {
    schema_version: "repo-async-production-control-completion@v1",
    status: "projected",
    projection_id: request.projection_id,
    projection_ref: projectionRef,
    projection_sha256: sha256(reopened),
    admission_eligible: false,
  };
}
