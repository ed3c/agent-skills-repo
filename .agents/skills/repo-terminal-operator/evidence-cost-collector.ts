import { createHash } from "node:crypto";
import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  realpathSync,
} from "node:fs";
import {
  basename,
  dirname,
  isAbsolute,
  join,
  relative,
  resolve,
  sep,
} from "node:path";
import { readAnchoredArtifact } from "./anchored-artifact-read";
import {
  canonicalEvidenceCostRequestBytes,
  parseEvidenceCostCacheRequest,
  type EvidenceCostCacheRequest,
} from "./evidence-cost-cache";
import { publishWriterArtifact } from "./writer-publication";
import { runMeasuredOwnedProfileCommand } from "../../../../../skills/repo-neural-perception/scripts/measured-owned-profile-command";

const MAX_INPUT_BYTES = 4 * 1024 * 1024;
const IDENTIFIER = /^[a-z0-9][a-z0-9._-]{0,127}$/u;
const SHA256 = /^[a-f0-9]{64}$/u;
const HEAD = /^[a-f0-9]{40,64}$/u;
const SAFE_REF = /^[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/u;
const NEXT_INTENT =
  "HARNESS-CROSS-CUTTING-EVIDENCE-COST-MEASUREMENT-COLLECTORS";
export const EVIDENCE_COST_COLLECTOR_BUNDLE_REFS = [
  "repo/agent-skills-repo/.agents/skills/repo-terminal-operator/evidence-cost-collector-cli.ts",
  "repo/agent-skills-repo/.agents/skills/repo-terminal-operator/evidence-cost-collector.ts",
  "repo/agent-skills-repo/.agents/skills/repo-terminal-operator/evidence-cost-cache.ts",
  "repo/agent-skills-repo/.agents/skills/repo-terminal-operator/anchored-artifact-read.ts",
  "repo/agent-skills-repo/.agents/skills/repo-terminal-operator/writer-native.ts",
  "repo/agent-skills-repo/.agents/skills/repo-terminal-operator/writer-native-library.ts",
  "repo/agent-skills-repo/.agents/skills/repo-terminal-operator/writer-publication.ts",
  "repo/agent-skills-repo/.agents/skills/repo-terminal-operator/writer-publication-contract.ts",
  "repo/agent-skills-repo/.agents/skills/repo-terminal-operator/writer-publication-lifecycle.ts",
  "repo/agent-skills-repo/.agents/skills/repo-terminal-operator/writer-publication-probe.ts",
  "repo/agent-skills-repo/.agents/skills/repo-terminal-operator/writer-publication-read.ts",
  "skills/repo-neural-perception/scripts/measured-owned-profile-command.ts",
  "skills/repo-neural-perception/scripts/owned-process-registry.ts",
  "skills/repo-neural-perception/scripts/owned-process-preload.cjs",
] as const;

type CollectorRequest = {
  schema_version: "repo-evidence-cost-collector-request@v1";
  collector_request_id: string;
  canonical_request: { ref: string; sha256: string };
  stage: { combination_id: string; stage_id: string };
  stage_progress: { ref: string; sha256: string };
  expected_repo_head: string;
  timeout_ms: number;
};

type RequestStage =
  EvidenceCostCacheRequest["combinations"][number]["stages"][number];

export type CollectorCompletion = {
  schema_version: "repo-evidence-cost-collector-completion@v1";
  status: "measured" | "failed";
  collector_request_id: string;
  collector_request: { ref: string; sha256: string };
  canonical_request: { ref: string; sha256: string };
  stage: { combination_id: string; stage_id: string };
  stage_progress: { ref: string; sha256: string };
  execution: { ref: string; sha256: string };
  observation: { ref: string; sha256: string } | null;
  measured_axes: string[];
  missing_axes: string[];
  next_mode: { id: string; prompt: string };
  admission_eligible: false;
  completion: { ref: string; sha256: string };
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
  keys: readonly string[],
  label: string,
): void {
  if (
    JSON.stringify(Object.keys(value).sort()) !==
    JSON.stringify([...keys].sort())
  )
    throw new Error(`${label}-fields-invalid`);
}

function identifier(value: unknown, label: string): string {
  if (typeof value !== "string" || !IDENTIFIER.test(value))
    throw new Error(`${label}-invalid`);
  return value;
}

function digest(value: unknown, label: string): string {
  if (typeof value !== "string" || !SHA256.test(value))
    throw new Error(`${label}-invalid`);
  return value;
}

function safeRef(value: unknown, label: string): string {
  if (
    typeof value !== "string" ||
    !SAFE_REF.test(value) ||
    value.split("/").some((part) => part === "." || part === "..")
  )
    throw new Error(`${label}-invalid`);
  return value;
}

function parseCollectorRequest(bytes: Buffer): CollectorRequest {
  if (bytes.byteLength > MAX_INPUT_BYTES)
    throw new Error("collector-request-too-large");
  const input = object(JSON.parse(bytes.toString("utf8")), "collector-request");
  exactKeys(
    input,
    [
      "schema_version",
      "collector_request_id",
      "canonical_request",
      "stage",
      "stage_progress",
      "expected_repo_head",
      "timeout_ms",
    ],
    "collector-request",
  );
  if (input.schema_version !== "repo-evidence-cost-collector-request@v1")
    throw new Error("collector-request-schema-invalid");
  const canonical = object(
    input.canonical_request,
    "collector-canonical-request",
  );
  exactKeys(canonical, ["ref", "sha256"], "collector-canonical-request");
  const canonicalSha256 = digest(
    canonical.sha256,
    "collector-canonical-sha256",
  );
  const canonicalRef = safeRef(canonical.ref, "collector-canonical-ref");
  if (canonicalRef !== `requests/${canonicalSha256}.json`)
    throw new Error("collector-canonical-content-address-mismatch");
  const stage = object(input.stage, "collector-stage");
  exactKeys(stage, ["combination_id", "stage_id"], "collector-stage");
  const progress = object(input.stage_progress, "collector-stage-progress");
  exactKeys(progress, ["ref", "sha256"], "collector-stage-progress");
  const progressRef = safeRef(progress.ref, "collector-progress-ref");
  if (!progressRef.startsWith("progress/"))
    throw new Error("collector-progress-namespace-invalid");
  if (
    typeof input.expected_repo_head !== "string" ||
    !HEAD.test(input.expected_repo_head)
  )
    throw new Error("collector-expected-head-invalid");
  if (
    !Number.isInteger(input.timeout_ms) ||
    Number(input.timeout_ms) < 1 ||
    Number(input.timeout_ms) > 900_000
  )
    throw new Error("collector-timeout-invalid");
  return {
    schema_version: input.schema_version,
    collector_request_id: identifier(
      input.collector_request_id,
      "collector-request-id",
    ),
    canonical_request: { ref: canonicalRef, sha256: canonicalSha256 },
    stage: {
      combination_id: identifier(
        stage.combination_id,
        "collector-combination-id",
      ),
      stage_id: identifier(stage.stage_id, "collector-stage-id"),
    },
    stage_progress: {
      ref: progressRef,
      sha256: digest(progress.sha256, "collector-progress-sha256"),
    },
    expected_repo_head: input.expected_repo_head,
    timeout_ms: Number(input.timeout_ms),
  };
}

function readBound(
  root: string,
  ref: string,
  expectedSha256: string,
  label: string,
): Buffer {
  const bytes = readAnchoredArtifact(root, ref, MAX_INPUT_BYTES, label);
  if (!bytes) throw new Error(`${label}-missing`);
  if (sha256(bytes) !== expectedSha256)
    throw new Error(`${label}-hash-mismatch`);
  return bytes;
}

function repoRef(repoRoot: string, path: string): string {
  const local = relative(repoRoot, path);
  if (local === ".." || local.startsWith(`..${sep}`) || local.length === 0)
    throw new Error("collector-source-outside-repo");
  return local;
}

async function runBoundedGit(
  repoRoot: string,
  args: string[],
  signal?: AbortSignal,
): Promise<string> {
  const git = Bun.which("git");
  if (!git) throw new Error("collector-git-missing");
  const result = await runMeasuredOwnedProfileCommand(
    [realpathSync(git), "-C", repoRoot, ...args],
    repoRoot,
    2_000,
    signal ? { signal } : {},
  );
  if (
    result.exitCode !== 0 ||
    result.timedOut ||
    result.cancelled ||
    result.stdoutTruncated ||
    result.stderrTruncated ||
    result.cleanupErrors.length > 0
  )
    throw new Error(
      `collector-git-probe-failed:${args.join(" ")}:${result.stderr.trim()}:${result.cleanupErrors.join(";")}`,
    );
  return result.stdout;
}

async function gitHead(
  repoRoot: string,
  signal?: AbortSignal,
): Promise<string> {
  const head = (
    await runBoundedGit(repoRoot, ["rev-parse", "HEAD"], signal)
  ).trim();
  if (!HEAD.test(head)) throw new Error("collector-git-head-invalid");
  return head;
}

async function gitBlob(
  repoRoot: string,
  head: string,
  ref: string,
  signal?: AbortSignal,
): Promise<Buffer> {
  try {
    return Buffer.from(
      await runBoundedGit(repoRoot, ["show", `${head}:${ref}`], signal),
    );
  } catch (error) {
    throw new Error(
      `collector-source-not-in-head:${ref}:${error instanceof Error ? error.message : String(error)}`,
      { cause: error },
    );
  }
}

function boundedExecutableSha256(path: string): string {
  const descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  let primary: unknown;
  let digest: string | undefined;
  try {
    const stat = fstatSync(descriptor);
    if (!stat.isFile() || stat.size > 512 * 1024 * 1024)
      throw new Error("collector-executable-not-bounded-regular-file");
    digest = sha256(readFileSync(descriptor));
  } catch (error) {
    primary = error;
  }
  try {
    closeSync(descriptor);
  } catch (cleanupError) {
    primary = primary
      ? new AggregateError(
          [primary, cleanupError],
          "collector executable read and cleanup failed",
        )
      : cleanupError;
  }
  if (primary) throw primary;
  if (!digest) throw new Error("collector-executable-hash-missing");
  return digest;
}

async function executableIdentity(
  stage: RequestStage,
  cwd: string,
  signal?: AbortSignal,
) {
  const requested = stage.toolchain.command[0];
  if (!requested) throw new Error("collector-executable-missing");
  const located = isAbsolute(requested) ? requested : Bun.which(requested);
  if (!located) throw new Error(`collector-executable-not-found:${requested}`);
  const resolvedPath = realpathSync(located);
  const version = await runMeasuredOwnedProfileCommand(
    [resolvedPath, "--version"],
    cwd,
    2_000,
    signal ? { signal } : {},
  );
  if (
    version.exitCode !== 0 ||
    version.timedOut ||
    version.cancelled ||
    version.cleanupErrors.length > 0
  )
    throw new Error(
      `collector-executable-version-failed:${version.stderr.trim()}:${version.cleanupErrors.join(";")}`,
    );
  const observedVersion = `${version.stdout}${version.stderr}`.trim();
  const expectedVersion = stage.toolchain.version;
  const versionMatches =
    observedVersion === expectedVersion ||
    observedVersion === `v${expectedVersion}`;
  if (!versionMatches)
    throw new Error(
      `collector-runtime-version-mismatch:expected=${expectedVersion}:observed=${observedVersion}`,
    );
  return {
    requested,
    resolved_path: resolvedPath,
    sha256: boundedExecutableSha256(resolvedPath),
    runtime: stage.toolchain.runtime,
    version: observedVersion,
  };
}

function locateStage(
  request: EvidenceCostCacheRequest,
  binding: CollectorRequest["stage"],
): RequestStage {
  const combination = request.combinations.find(
    (candidate) => candidate.combination_id === binding.combination_id,
  );
  if (!combination) throw new Error("collector-combination-not-found");
  const stage = combination.stages.find(
    (candidate) => candidate.stage_id === binding.stage_id,
  );
  if (!stage) throw new Error("collector-stage-not-found");
  return stage;
}

function validateProgress(
  bytes: Buffer,
  request: EvidenceCostCacheRequest,
  collector: CollectorRequest,
  stage: RequestStage,
): void {
  const progress = object(
    JSON.parse(bytes.toString("utf8")),
    "collector-stage-progress",
  );
  exactKeys(
    progress,
    [
      "schema_version",
      "status",
      "request_id",
      "combination_id",
      "stage_id",
      "stage_request_sha256",
    ],
    "collector-stage-progress",
  );
  if (
    progress.schema_version !== "repo-evidence-stage-progress@v1" ||
    progress.status !== "ready" ||
    progress.request_id !== request.request_id ||
    progress.combination_id !== collector.stage.combination_id ||
    progress.stage_id !== collector.stage.stage_id ||
    progress.stage_request_sha256 !== sha256(JSON.stringify(stage))
  )
    throw new Error("collector-stage-progress-binding-mismatch");
}

async function sourceIdentity(
  repoRoot: string,
  head: string,
  path: string,
  signal?: AbortSignal,
) {
  const ref = repoRef(repoRoot, path);
  const bytes = readAnchoredArtifact(
    repoRoot,
    ref,
    MAX_INPUT_BYTES,
    "collector-source",
  );
  if (!bytes) throw new Error("collector-source-missing");
  const currentSha256 = sha256(bytes);
  if (sha256(await gitBlob(repoRoot, head, ref, signal)) !== currentSha256)
    throw new Error(`collector-source-dirty-against-head:${ref}`);
  return { ref, sha256: currentSha256 };
}

function assertCanonicalInputs(
  stateRoot: string,
  request: EvidenceCostCacheRequest,
  stage: RequestStage,
): void {
  const plan = readBound(
    stateRoot,
    request.plan.ref,
    request.plan.sha256,
    "collector-plan",
  );
  if (plan.byteLength === 0) throw new Error("collector-plan-empty");
  for (const source of request.source_inputs)
    readBound(
      stateRoot,
      source.ref,
      source.sha256,
      `collector-source-input:${source.input_id}`,
    );
  const evidence = readBound(
    stateRoot,
    stage.evidence_ref,
    stage.evidence_sha256,
    `collector-stage-evidence:${stage.stage_id}`,
  );
  const receipt = object(
    JSON.parse(evidence.toString("utf8")),
    "collector-stage-evidence",
  );
  if (typeof receipt.schema_version !== "string" || receipt.status !== "passed")
    throw new Error("collector-stage-evidence-not-passing-typed-receipt");
}

function ensurePublicationDirectory(root: string, namespace: string): void {
  let parent = root;
  for (const component of namespace.split("/")) {
    parent = join(parent, component);
    try {
      mkdirSync(parent, { mode: 0o700 });
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
    }
    const stat = lstatSync(parent);
    if (!stat.isDirectory() || stat.isSymbolicLink())
      throw new Error(`collector-output-directory-unsafe:${component}`);
  }
}

function publishContentAddressed(
  stateRoot: string,
  namespace: string,
  value: unknown,
): { ref: string; sha256: string } {
  ensurePublicationDirectory(stateRoot, namespace);
  const bytes = Buffer.from(`${JSON.stringify(value, null, 2)}\n`);
  const artifactSha256 = sha256(bytes);
  const ref = `${namespace}/${artifactSha256}.json`;
  publishWriterArtifact(stateRoot, join(stateRoot, ref), bytes);
  const reopened = readBound(stateRoot, ref, artifactSha256, namespace);
  if (!reopened.equals(bytes))
    throw new Error(`${namespace}-publication-mismatch`);
  return { ref, sha256: artifactSha256 };
}

function publishContentAddressedBytes(
  stateRoot: string,
  namespace: string,
  bytes: Buffer,
): { ref: string; sha256: string } {
  ensurePublicationDirectory(stateRoot, namespace);
  const artifactSha256 = sha256(bytes);
  const ref = `${namespace}/${artifactSha256}.json`;
  publishWriterArtifact(stateRoot, join(stateRoot, ref), bytes);
  const reopened = readBound(stateRoot, ref, artifactSha256, namespace);
  if (!reopened.equals(bytes))
    throw new Error(`${namespace}-publication-mismatch`);
  return { ref, sha256: artifactSha256 };
}

export async function collectEvidenceCost(
  stateRoot: string,
  repoRoot: string,
  collectorRequestPath: string,
  options: { signal?: AbortSignal; beforeCommit?: () => void } = {},
): Promise<CollectorCompletion> {
  const requestBytes = readAnchoredArtifact(
    dirname(collectorRequestPath),
    basename(collectorRequestPath),
    MAX_INPUT_BYTES,
    "collector-request",
  );
  if (!requestBytes) throw new Error("collector-request-missing");
  const collector = parseCollectorRequest(requestBytes);
  const collectorRequest = publishContentAddressedBytes(
    stateRoot,
    "collector/requests",
    requestBytes,
  );
  const canonicalBytes = readBound(
    stateRoot,
    collector.canonical_request.ref,
    collector.canonical_request.sha256,
    "collector-canonical-request",
  );
  const canonical = parseEvidenceCostCacheRequest(canonicalBytes);
  if (!canonicalBytes.equals(canonicalEvidenceCostRequestBytes(canonical)))
    throw new Error("collector-canonical-request-not-canonical");
  const stage = locateStage(canonical, collector.stage);
  assertCanonicalInputs(stateRoot, canonical, stage);
  const progressBytes = readBound(
    stateRoot,
    collector.stage_progress.ref,
    collector.stage_progress.sha256,
    "collector-stage-progress",
  );
  validateProgress(progressBytes, canonical, collector, stage);
  const head = await gitHead(repoRoot, options.signal);
  if (head !== collector.expected_repo_head)
    throw new Error("collector-repo-head-mismatch");
  const collectorBundle = [];
  for (const ref of EVIDENCE_COST_COLLECTOR_BUNDLE_REFS)
    collectorBundle.push(
      await sourceIdentity(
        repoRoot,
        head,
        resolve(repoRoot, ref),
        options.signal,
      ),
    );
  const executable = await executableIdentity(stage, repoRoot, options.signal);
  const actualArgv = [
    executable.resolved_path,
    ...stage.toolchain.command.slice(1),
  ];
  const startedAt = new Date().toISOString();
  const result = await runMeasuredOwnedProfileCommand(
    actualArgv,
    repoRoot,
    collector.timeout_ms,
    options.signal ? { signal: options.signal } : {},
  );
  const completedAt = new Date().toISOString();
  // Receipt finalization is a bounded cleanup phase. It must survive the stage's
  // cancelled signal so cancellation itself can be persisted and diagnosed.
  const completedRepoHead = await gitHead(repoRoot);
  // Drain queued signal events, snapshot cancellation, then remove the CLI's
  // custom handlers. Any later OS signal fail-stops the synchronous publication
  // window instead of being acknowledged after a measured receipt is emitted.
  await Bun.sleep(0);
  options.beforeCommit?.();
  const cancelled = result.cancelled || options.signal?.aborted === true;
  const headStable = completedRepoHead === head;
  const passed =
    result.exitCode === 0 &&
    !result.timedOut &&
    !cancelled &&
    result.processReaped &&
    result.timerCleared &&
    result.stdoutConsumed &&
    result.stderrConsumed &&
    result.ownershipSentinelConsumed &&
    result.ownershipRegistryRemoved &&
    result.cpuAvailable &&
    headStable &&
    result.cleanupErrors.length === 0;
  const execution = {
    schema_version: "repo-evidence-cost-collector-execution@v1",
    status: passed ? "passed" : "failed",
    failure_kind: passed
      ? null
      : result.timedOut
        ? "timeout"
        : cancelled
          ? "cancelled"
          : result.exitCode !== 0
            ? "command-failed"
            : !headStable
              ? "repository-head-drift"
              : "cleanup-failed",
    collector_request: collectorRequest,
    canonical_request: collector.canonical_request,
    stage: collector.stage,
    stage_progress: collector.stage_progress,
    repo_head: head,
    completed_repo_head: completedRepoHead,
    stage_source_closure: {
      status: "not-selected",
      reason:
        "collector binds its own bundle and executable; the stage worktree/source closure is not sealed",
    },
    collector_bundle: collectorBundle,
    argv: stage.toolchain.command,
    actual_argv: actualArgv,
    executable,
    started_at: startedAt,
    completed_at: completedAt,
    exit_code: result.exitCode,
    signal: result.signal,
    timed_out: result.timedOut,
    cancelled,
    wall_ms: result.wallMs,
    cpu: {
      available: result.cpuAvailable,
      scope: result.cpuScope,
      user_ms: result.cpuUserMs,
      system_ms: result.cpuSystemMs,
      total_ms: result.cpuTotalMs,
    },
    io_operations: {
      available: result.ioOperationsAvailable,
      input: result.ioOperationsIn,
      output: result.ioOperationsOut,
      bytes_available: false,
    },
    output: {
      stdout_tail: result.stdout,
      stderr_tail: result.stderr,
      stdout_truncated: result.stdoutTruncated,
      stderr_truncated: result.stderrTruncated,
      stdout_tail_sha256: sha256(Buffer.from(result.stdout)),
      stderr_tail_sha256: sha256(Buffer.from(result.stderr)),
    },
    cleanup: {
      process_reaped: result.processReaped,
      timer_cleared: result.timerCleared,
      stdout_consumed: result.stdoutConsumed,
      stderr_consumed: result.stderrConsumed,
      ownership_sentinel_consumed: result.ownershipSentinelConsumed,
      ownership_registry_removed: result.ownershipRegistryRemoved,
      errors: result.cleanupErrors,
    },
    admission_eligible: false,
  };
  const executionArtifact = publishContentAddressed(
    stateRoot,
    "collector/executions",
    execution,
  );
  let observation: { ref: string; sha256: string } | null = null;
  if (passed) {
    if (
      result.cpuUserMs === null ||
      result.cpuSystemMs === null ||
      result.cpuTotalMs === null
    )
      throw new Error("collector-cpu-measurement-missing-after-pass");
    observation = publishContentAddressed(stateRoot, "observations", {
      schema_version: "repo-evidence-cost-observation@v2",
      status: "passed",
      claim_boundary: "trusted-direct-process-collector/axis-scoped",
      stage_id: stage.stage_id,
      evidence_sha256: stage.evidence_sha256,
      toolchain: stage.toolchain,
      stage_source_closure: execution.stage_source_closure,
      collector: {
        collector_request: collectorRequest,
        canonical_request: collector.canonical_request,
        stage_progress: collector.stage_progress,
        execution: executionArtifact,
        repo_head: head,
        collector_bundle: collectorBundle,
      },
      cost: {
        wall_ms: result.wallMs,
        cpu: {
          status: "measured",
          scope: "direct-child",
          user_ms: result.cpuUserMs,
          system_ms: result.cpuSystemMs,
        },
        io: { status: "not-selected", read_bytes: null, write_bytes: null },
        llm: {
          status: "not-selected",
          input_tokens: null,
          output_tokens: null,
        },
      },
      admission_eligible: false,
    });
  }
  const missingAxes = passed
    ? ["io-bytes", "llm-tokens"]
    : ["wall", "cpu-direct-child", "io-bytes", "llm-tokens"];
  const completion = {
    schema_version: "repo-evidence-cost-collector-completion@v1" as const,
    status: passed ? ("measured" as const) : ("failed" as const),
    collector_request_id: collector.collector_request_id,
    collector_request: collectorRequest,
    canonical_request: collector.canonical_request,
    stage: collector.stage,
    stage_progress: collector.stage_progress,
    execution: executionArtifact,
    observation,
    measured_axes: passed ? ["wall", "cpu-direct-child"] : [],
    missing_axes: missingAxes,
    next_mode: {
      id: "evidence-cost/measure-missing-axes",
      prompt: `Intent-Slice: ${NEXT_INTENT}。量測 ${missingAxes
        .map((axis) => `${stage.stage_id}:${axis}`)
        .join(
          ", ",
        )}；不得以 I/O operations 或 0 代替 bytes/tokens，production collector 維持 opt-in。`,
    },
    admission_eligible: false as const,
  };
  const completionArtifact = publishContentAddressed(
    stateRoot,
    "collector/completions",
    completion,
  );
  return { ...completion, completion: completionArtifact };
}

export async function persistEvidenceCostPreconditionFailure(
  stateRoot: string,
  repoRoot: string,
  collectorRequestPath: string,
  reason: unknown,
  signal?: AbortSignal,
): Promise<{ ref: string; sha256: string }> {
  const requestBytes = readAnchoredArtifact(
    dirname(collectorRequestPath),
    basename(collectorRequestPath),
    MAX_INPUT_BYTES,
    "collector-failure-request",
  );
  if (!requestBytes) throw new Error("collector-failure-request-missing");
  const request = parseCollectorRequest(requestBytes);
  const collectorRequest = publishContentAddressedBytes(
    stateRoot,
    "collector/requests",
    requestBytes,
  );
  let observedRepoHead: string | null = null;
  let repoHeadDiagnostic: string | null = null;
  try {
    observedRepoHead = await gitHead(repoRoot, signal);
  } catch (error) {
    repoHeadDiagnostic = error instanceof Error ? error.message : String(error);
  }
  return publishContentAddressed(stateRoot, "collector/failures", {
    schema_version: "repo-evidence-cost-collector-precondition-failure@v1",
    status: "failed",
    failure_kind: "precondition-or-system",
    diagnostic: reason instanceof Error ? reason.message : String(reason),
    collector_request_id: request.collector_request_id,
    collector_request: collectorRequest,
    canonical_request: request.canonical_request,
    stage: request.stage,
    stage_progress: request.stage_progress,
    expected_repo_head: request.expected_repo_head,
    observed_repo_head: observedRepoHead,
    repo_head_diagnostic: repoHeadDiagnostic,
    admission_eligible: false,
  });
}
