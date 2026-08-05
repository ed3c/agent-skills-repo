import { createHash } from "node:crypto";
import { basename, dirname, join } from "node:path";
import { publishWriterArtifact } from "./writer-publication";
import { readAnchoredArtifact } from "./anchored-artifact-read";

const IDENTIFIER = /^[a-z0-9][a-z0-9._-]{0,127}$/u;
const SHA256 = /^[a-f0-9]{64}$/u;
const HARNESS_INTENT = /^HARNESS-CROSS-CUTTING-[A-Z0-9-]+$/u;
const MAX_REQUEST_BYTES = 4 * 1024 * 1024;
const MAX_EVIDENCE_BYTES = 16 * 1024 * 1024;
const SAFE_REF = /^[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*$/u;

type AxisStatus = "measured" | "not-selected" | "not-applicable";
type Purpose =
  | "runtime"
  | "migration"
  | "parity"
  | "code-quality"
  | "production-use"
  | "evidence"
  | "release";
type Ecosystem = "bun" | "python" | "browser" | "agy" | "mixed";
type FunctionalMode =
  | "instant"
  | "focused"
  | "confidence"
  | "migration"
  | "parity"
  | "audit"
  | "release"
  | "code-quality"
  | "production-use";

type CpuCost = {
  status: AxisStatus;
  user_ms: number | null;
  system_ms: number | null;
};
type IoCost = {
  status: AxisStatus;
  read_bytes: number | null;
  write_bytes: number | null;
};
type LlmCost = {
  status: AxisStatus;
  input_tokens: number | null;
  output_tokens: number | null;
};
type StageCost = {
  wall_ms: number;
  cpu: CpuCost;
  io: IoCost;
  llm: LlmCost;
};
type RequestStage = {
  stage_id: string;
  purpose: Purpose;
  ecosystem: Ecosystem;
  evidence_ref: string;
  evidence_sha256: string;
  source_input_ids: string[];
  oracle_relation:
    | "same-contract"
    | "primary-subset"
    | "adjacent-contract"
    | "not-required";
  toolchain: { runtime: string; version: string; command: string[] };
  cost_observation_ref: string;
  cost_observation_sha256: string;
};
type EvidenceStage = RequestStage & {
  cost: StageCost;
};
type Combination = {
  combination_id: string;
  selected_modes: FunctionalMode[];
  stages: RequestStage[];
};
type SourceInput = {
  input_id: string;
  kind: "dr" | "gcr" | "user" | "generic";
  ref: string;
  sha256: string;
};
export type EvidenceCostCacheRequest = {
  schema_version: "repo-evidence-cost-cache-request@v1";
  request_id: string;
  mode: "project-only";
  observed_at: string;
  source_inputs: SourceInput[];
  plan: { intent_slice: string; ref: string; sha256: string };
  combinations: Combination[];
  activation: { forgejo_enabled: false; cloud_enabled: false };
};

type CacheStatus = "miss" | "persistent-hit" | "cross-combination-hit";
type CacheEntry = {
  schema_version: "repo-evidence-cost-cache-entry@v1";
  cache_key: string;
  created_by_request_sha256: string;
  status: "passed";
  stage: EvidenceStage;
  trace: {
    plan: EvidenceCostCacheRequest["plan"];
    source_inputs: SourceInput[];
  };
};
type CacheIdentity = Pick<CacheEntry, "stage" | "trace">;

export type EvidenceCostCacheCompletion = {
  schema_version: "repo-evidence-cost-cache-completion@v1";
  status: "projected";
  request_id: string;
  request_ref: string;
  request_sha256: string;
  ledger_ref: string;
  ledger_sha256: string;
  cache: {
    unique_entries: number;
    misses: number;
    persistent_hits: number;
    cross_combination_hits: number;
  };
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
  keys: readonly string[],
  label: string,
): void {
  if (
    JSON.stringify(Object.keys(value).sort()) !==
    JSON.stringify([...keys].sort())
  )
    throw new Error(`${label}-fields-invalid`);
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0)
    throw new Error(`${label}-invalid`);
  return value;
}

function identifier(value: unknown, label: string): string {
  const result = text(value, label);
  if (!IDENTIFIER.test(result)) throw new Error(`${label}-invalid`);
  return result;
}

function digest(value: unknown, label: string): string {
  const result = text(value, label);
  if (!SHA256.test(result)) throw new Error(`${label}-invalid`);
  return result;
}

function ref(value: unknown, label: string): string {
  const result = text(value, label);
  if (
    !SAFE_REF.test(result) ||
    result.split("/").some((part) => part === "." || part === "..")
  )
    throw new Error(`${label}-invalid`);
  return result;
}

function finite(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0)
    throw new Error(`${label}-invalid`);
  return value;
}

function nullableFinite(value: unknown, label: string): number | null {
  return value === null ? null : finite(value, label);
}

function strings(value: unknown, label: string, maximum = 64): string[] {
  if (
    !Array.isArray(value) ||
    value.length === 0 ||
    value.length > maximum ||
    !value.every((entry) => typeof entry === "string" && entry.length > 0) ||
    new Set(value).size !== value.length
  )
    throw new Error(`${label}-invalid`);
  return [...value] as string[];
}

function axisStatus(value: unknown, label: string): AxisStatus {
  if (!["measured", "not-selected", "not-applicable"].includes(String(value)))
    throw new Error(`${label}-invalid`);
  return value as AxisStatus;
}

function assertAxisValues(
  status: AxisStatus,
  values: readonly (number | null)[],
  label: string,
): void {
  if (status === "measured" && values.some((value) => value === null))
    throw new Error(`${label}-measured-values-required`);
  if (status !== "measured" && values.some((value) => value !== null))
    throw new Error(`${label}-unmeasured-values-must-be-null`);
}

function parseCost(value: unknown, label: string): StageCost {
  const input = object(value, label);
  exactKeys(input, ["wall_ms", "cpu", "io", "llm"], label);
  const cpu = object(input.cpu, `${label}-cpu`);
  exactKeys(cpu, ["status", "user_ms", "system_ms"], `${label}-cpu`);
  const io = object(input.io, `${label}-io`);
  exactKeys(io, ["status", "read_bytes", "write_bytes"], `${label}-io`);
  const llm = object(input.llm, `${label}-llm`);
  exactKeys(llm, ["status", "input_tokens", "output_tokens"], `${label}-llm`);
  const result: StageCost = {
    wall_ms: finite(input.wall_ms, `${label}-wall-ms`),
    cpu: {
      status: axisStatus(cpu.status, `${label}-cpu-status`),
      user_ms: nullableFinite(cpu.user_ms, `${label}-cpu-user-ms`),
      system_ms: nullableFinite(cpu.system_ms, `${label}-cpu-system-ms`),
    },
    io: {
      status: axisStatus(io.status, `${label}-io-status`),
      read_bytes: nullableFinite(io.read_bytes, `${label}-io-read-bytes`),
      write_bytes: nullableFinite(io.write_bytes, `${label}-io-write-bytes`),
    },
    llm: {
      status: axisStatus(llm.status, `${label}-llm-status`),
      input_tokens: nullableFinite(llm.input_tokens, `${label}-llm-input`),
      output_tokens: nullableFinite(llm.output_tokens, `${label}-llm-output`),
    },
  };
  assertAxisValues(
    result.cpu.status,
    [result.cpu.user_ms, result.cpu.system_ms],
    `${label}-cpu`,
  );
  assertAxisValues(
    result.io.status,
    [result.io.read_bytes, result.io.write_bytes],
    `${label}-io`,
  );
  assertAxisValues(
    result.llm.status,
    [result.llm.input_tokens, result.llm.output_tokens],
    `${label}-llm`,
  );
  return result;
}

function oneOf<T extends string>(
  value: unknown,
  allowed: readonly T[],
  label: string,
): T {
  if (!allowed.includes(value as T)) throw new Error(`${label}-invalid`);
  return value as T;
}

function parseStage(value: unknown, label: string): RequestStage {
  const input = object(value, label);
  exactKeys(
    input,
    [
      "stage_id",
      "purpose",
      "ecosystem",
      "evidence_ref",
      "evidence_sha256",
      "source_input_ids",
      "oracle_relation",
      "toolchain",
      "cost_observation_ref",
      "cost_observation_sha256",
    ],
    label,
  );
  const toolchain = object(input.toolchain, `${label}-toolchain`);
  exactKeys(toolchain, ["runtime", "version", "command"], `${label}-toolchain`);
  return {
    stage_id: identifier(input.stage_id, `${label}-stage-id`),
    purpose: oneOf(
      input.purpose,
      [
        "runtime",
        "migration",
        "parity",
        "code-quality",
        "production-use",
        "evidence",
        "release",
      ],
      `${label}-purpose`,
    ),
    ecosystem: oneOf(
      input.ecosystem,
      ["bun", "python", "browser", "agy", "mixed"],
      `${label}-ecosystem`,
    ),
    evidence_ref: ref(input.evidence_ref, `${label}-evidence-ref`),
    evidence_sha256: digest(input.evidence_sha256, `${label}-evidence-sha256`),
    source_input_ids: strings(
      input.source_input_ids,
      `${label}-source-input-ids`,
    ).sort(),
    oracle_relation: oneOf(
      input.oracle_relation,
      ["same-contract", "primary-subset", "adjacent-contract", "not-required"],
      `${label}-oracle-relation`,
    ),
    toolchain: {
      runtime: text(toolchain.runtime, `${label}-runtime`),
      version: text(toolchain.version, `${label}-version`),
      command: strings(toolchain.command, `${label}-command`, 128),
    },
    cost_observation_ref: ref(
      input.cost_observation_ref,
      `${label}-cost-observation-ref`,
    ),
    cost_observation_sha256: digest(
      input.cost_observation_sha256,
      `${label}-cost-observation-sha256`,
    ),
  };
}

function parseSource(value: unknown, label: string): SourceInput {
  const input = object(value, label);
  exactKeys(input, ["input_id", "kind", "ref", "sha256"], label);
  return {
    input_id: identifier(input.input_id, `${label}-input-id`),
    kind: oneOf(input.kind, ["dr", "gcr", "user", "generic"], `${label}-kind`),
    ref: ref(input.ref, `${label}-ref`),
    sha256: digest(input.sha256, `${label}-sha256`),
  };
}

function parseCombination(value: unknown, label: string): Combination {
  const input = object(value, label);
  exactKeys(input, ["combination_id", "selected_modes", "stages"], label);
  const modes: FunctionalMode[] = strings(
    input.selected_modes,
    `${label}-selected-modes`,
  ).map((mode) =>
    oneOf<FunctionalMode>(
      mode,
      [
        "instant",
        "focused",
        "confidence",
        "migration",
        "parity",
        "audit",
        "release",
        "code-quality",
        "production-use",
      ],
      `${label}-selected-mode`,
    ),
  );
  if (
    !Array.isArray(input.stages) ||
    input.stages.length === 0 ||
    input.stages.length > 256
  )
    throw new Error(`${label}-stages-invalid`);
  const stages = input.stages.map((stage, index) =>
    parseStage(stage, `${label}-stage-${String(index)}`),
  );
  if (new Set(stages.map((stage) => stage.stage_id)).size !== stages.length)
    throw new Error(`${label}-stage-ids-not-unique`);
  return {
    combination_id: identifier(input.combination_id, `${label}-combination-id`),
    selected_modes: modes.sort(),
    stages: stages.sort((left, right) =>
      left.stage_id.localeCompare(right.stage_id),
    ),
  };
}

export function parseEvidenceCostCacheRequest(
  bytes: Uint8Array,
): EvidenceCostCacheRequest {
  if (bytes.byteLength > MAX_REQUEST_BYTES)
    throw new Error("evidence-cost-request-too-large");
  const input = object(
    JSON.parse(Buffer.from(bytes).toString("utf8")),
    "evidence-cost-request",
  );
  exactKeys(
    input,
    [
      "schema_version",
      "request_id",
      "mode",
      "observed_at",
      "source_inputs",
      "plan",
      "combinations",
      "activation",
    ],
    "evidence-cost-request",
  );
  if (
    input.schema_version !== "repo-evidence-cost-cache-request@v1" ||
    input.mode !== "project-only" ||
    typeof input.observed_at !== "string" ||
    new Date(input.observed_at).toISOString() !== input.observed_at
  )
    throw new Error("evidence-cost-request-contract-invalid");
  if (
    !Array.isArray(input.source_inputs) ||
    input.source_inputs.length === 0 ||
    input.source_inputs.length > 256
  )
    throw new Error("evidence-cost-source-inputs-invalid");
  const sources = input.source_inputs.map((source, index) =>
    parseSource(source, `source-${String(index)}`),
  );
  if (new Set(sources.map((source) => source.input_id)).size !== sources.length)
    throw new Error("evidence-cost-source-input-ids-not-unique");
  const plan = object(input.plan, "evidence-cost-plan");
  exactKeys(plan, ["intent_slice", "ref", "sha256"], "evidence-cost-plan");
  const activation = object(input.activation, "evidence-cost-activation");
  exactKeys(
    activation,
    ["forgejo_enabled", "cloud_enabled"],
    "evidence-cost-activation",
  );
  if (
    activation.forgejo_enabled !== false ||
    activation.cloud_enabled !== false
  )
    throw new Error("evidence-cost-activation-must-remain-disabled");
  const intentSlice = text(plan.intent_slice, "evidence-cost-intent-slice");
  if (!HARNESS_INTENT.test(intentSlice))
    throw new Error("evidence-cost-intent-slice-invalid");
  if (
    !Array.isArray(input.combinations) ||
    input.combinations.length === 0 ||
    input.combinations.length > 64
  )
    throw new Error("evidence-cost-combinations-invalid");
  const combinations = input.combinations.map((combination, index) =>
    parseCombination(combination, `combination-${String(index)}`),
  );
  if (
    new Set(combinations.map((combination) => combination.combination_id))
      .size !== combinations.length
  )
    throw new Error("evidence-cost-combination-ids-not-unique");
  const sourceIds = new Set(sources.map((source) => source.input_id));
  const usedSources = new Set(
    combinations.flatMap((combination) =>
      combination.stages.flatMap((stage) => stage.source_input_ids),
    ),
  );
  if ([...usedSources].some((inputId) => !sourceIds.has(inputId)))
    throw new Error("evidence-cost-stage-source-input-unknown");
  if ([...sourceIds].some((inputId) => !usedSources.has(inputId)))
    throw new Error("evidence-cost-source-input-unbound");
  return {
    schema_version: input.schema_version,
    request_id: identifier(input.request_id, "evidence-cost-request-id"),
    mode: input.mode,
    observed_at: input.observed_at,
    source_inputs: sources.sort((left, right) =>
      left.input_id.localeCompare(right.input_id),
    ),
    plan: {
      intent_slice: intentSlice,
      ref: ref(plan.ref, "evidence-cost-plan-ref"),
      sha256: digest(plan.sha256, "evidence-cost-plan-sha256"),
    },
    combinations: combinations.sort((left, right) =>
      left.combination_id.localeCompare(right.combination_id),
    ),
    activation: { forgejo_enabled: false, cloud_enabled: false },
  };
}

export function readEvidenceCostCacheRequest(path: string): Buffer {
  const bytes = readAnchoredArtifact(
    dirname(path),
    basename(path),
    MAX_REQUEST_BYTES,
    "evidence-cost-request",
  );
  if (!bytes) throw new Error("evidence-cost-request-missing");
  return bytes;
}

export function canonicalEvidenceCostRequestBytes(
  request: EvidenceCostCacheRequest,
): Buffer {
  return Buffer.from(`${JSON.stringify(request, null, 2)}\n`);
}

function readBound(
  stateRoot: string,
  refValue: string,
  expectedSha256: string,
  label: string,
): Buffer {
  const bytes = readAnchoredArtifact(
    stateRoot,
    refValue,
    MAX_EVIDENCE_BYTES,
    label,
  );
  if (!bytes) throw new Error(`${label}-missing`);
  if (sha256(bytes) !== expectedSha256)
    throw new Error(`${label}-hash-mismatch`);
  return bytes;
}

function assertPassingEvidence(bytes: Buffer, label: string): void {
  const value = object(JSON.parse(bytes.toString("utf8")), label);
  if (typeof value.schema_version !== "string" || value.status !== "passed")
    throw new Error(`${label}-not-passing-typed-evidence`);
}

function materializeStage(
  stateRoot: string,
  stage: RequestStage,
): EvidenceStage {
  const label = `evidence-cost-observation:${stage.stage_id}`;
  const bytes = readBound(
    stateRoot,
    stage.cost_observation_ref,
    stage.cost_observation_sha256,
    label,
  );
  const observation = object(JSON.parse(bytes.toString("utf8")), label);
  exactKeys(
    observation,
    [
      "schema_version",
      "status",
      "claim_boundary",
      "stage_id",
      "evidence_sha256",
      "toolchain",
      "cost",
    ],
    label,
  );
  if (
    observation.schema_version !== "repo-evidence-cost-observation@v1" ||
    observation.status !== "passed" ||
    observation.claim_boundary !==
      "external-hash-bound-observation/unadmitted-collector" ||
    observation.stage_id !== stage.stage_id ||
    observation.evidence_sha256 !== stage.evidence_sha256 ||
    JSON.stringify(observation.toolchain) !== JSON.stringify(stage.toolchain)
  )
    throw new Error(`${label}-binding-mismatch`);
  return {
    ...stage,
    cost: parseCost(observation.cost, `${label}-cost`),
  };
}

function cacheIdentity(
  request: EvidenceCostCacheRequest,
  stage: EvidenceStage,
  sources: Map<string, SourceInput>,
): CacheIdentity {
  return {
    stage,
    trace: {
      plan: request.plan,
      source_inputs: stage.source_input_ids.map((inputId) => {
        const source = sources.get(inputId);
        if (!source) throw new Error(`evidence-cost-source-missing:${inputId}`);
        return source;
      }),
    },
  };
}

function cacheEntryBytes(
  identity: CacheIdentity,
  requestSha256: string,
): { key: string; bytes: Buffer } {
  const stableIdentity = { stage: identity.stage, trace: identity.trace };
  const key = sha256(JSON.stringify(stableIdentity));
  return {
    key,
    bytes: Buffer.from(
      `${JSON.stringify(
        {
          schema_version: "repo-evidence-cost-cache-entry@v1",
          cache_key: key,
          created_by_request_sha256: requestSha256,
          status: "passed",
          ...stableIdentity,
        } satisfies CacheEntry,
        null,
        2,
      )}\n`,
    ),
  };
}

function validateCacheEntry(
  bytes: Buffer,
  identity: CacheIdentity,
  key: string,
  label: string,
): CacheEntry {
  const entry = object(JSON.parse(bytes.toString("utf8")), label);
  exactKeys(
    entry,
    [
      "schema_version",
      "cache_key",
      "created_by_request_sha256",
      "status",
      "stage",
      "trace",
    ],
    label,
  );
  if (
    entry.schema_version !== "repo-evidence-cost-cache-entry@v1" ||
    entry.cache_key !== key ||
    entry.status !== "passed" ||
    !SHA256.test(String(entry.created_by_request_sha256)) ||
    JSON.stringify({ stage: entry.stage, trace: entry.trace }) !==
      JSON.stringify({ stage: identity.stage, trace: identity.trace })
  )
    throw new Error(`${label}-binding-mismatch`);
  return entry as CacheEntry;
}

function publishAndReopen(
  stateRoot: string,
  artifactRef: string,
  bytes: Buffer,
  label: string,
): Buffer {
  let publicationFailure: unknown;
  try {
    publishWriterArtifact(stateRoot, join(stateRoot, artifactRef), bytes);
  } catch (error) {
    publicationFailure = error;
  }
  const reopened = readAnchoredArtifact(
    stateRoot,
    artifactRef,
    MAX_EVIDENCE_BYTES,
    label,
  );
  if (reopened) return reopened;
  throw new Error(`${label}-publication-missing`, {
    cause: publicationFailure,
  });
}

function missingAxes(stage: EvidenceStage): string[] {
  return (["cpu", "io", "llm"] as const)
    .filter((axis) => stage.cost[axis].status === "not-selected")
    .map((axis) => `${stage.stage_id}:${axis}`);
}

function numeric(value: number | null): number {
  return value ?? 0;
}

export function projectEvidenceCostCache(
  stateRoot: string,
  request: EvidenceCostCacheRequest,
): EvidenceCostCacheCompletion {
  const planBytes = readBound(
    stateRoot,
    request.plan.ref,
    request.plan.sha256,
    "evidence-cost-plan",
  );
  if (planBytes.byteLength === 0) throw new Error("evidence-cost-plan-empty");
  const sourceMap = new Map(
    request.source_inputs.map((source) => {
      readBound(
        stateRoot,
        source.ref,
        source.sha256,
        `evidence-cost-source:${source.input_id}`,
      );
      return [source.input_id, source] as const;
    }),
  );
  const requestBytes = canonicalEvidenceCostRequestBytes(request);
  const canonicalRequestSha256 = sha256(requestBytes);
  const requestRef = `requests/${canonicalRequestSha256}.json`;
  const reopenedRequest = publishAndReopen(
    stateRoot,
    requestRef,
    requestBytes,
    "evidence-cost-canonical-request",
  );
  if (!reopenedRequest.equals(requestBytes))
    throw new Error("evidence-cost-canonical-request-publication-mismatch");
  const seen = new Set<string>();
  const unique = new Map<string, CacheEntry>();
  let misses = 0;
  let persistentHits = 0;
  let crossCombinationHits = 0;
  let observedWallMs = 0;
  const combinations = request.combinations.map((combination) => ({
    combination_id: combination.combination_id,
    selected_modes: combination.selected_modes,
    stages: combination.stages.map((requestStage) => {
      const stage = materializeStage(stateRoot, requestStage);
      const evidence = readBound(
        stateRoot,
        stage.evidence_ref,
        stage.evidence_sha256,
        `evidence-cost-stage:${stage.stage_id}`,
      );
      assertPassingEvidence(evidence, `evidence-cost-stage:${stage.stage_id}`);
      const identity = cacheIdentity(request, stage, sourceMap);
      const candidate = cacheEntryBytes(identity, canonicalRequestSha256);
      const entryRef = `cache/${candidate.key}.json`;
      const existing = readAnchoredArtifact(
        stateRoot,
        entryRef,
        MAX_EVIDENCE_BYTES,
        `evidence-cost-cache:${stage.stage_id}`,
      );
      let cacheStatus: CacheStatus;
      if (seen.has(candidate.key)) {
        if (!existing)
          throw new Error(
            `evidence-cost-cache-reopen-missing:${stage.stage_id}`,
          );
        validateCacheEntry(
          existing,
          identity,
          candidate.key,
          `evidence-cost-cache:${stage.stage_id}`,
        );
        cacheStatus = "cross-combination-hit";
        crossCombinationHits += 1;
      } else if (existing) {
        const entry = validateCacheEntry(
          existing,
          identity,
          candidate.key,
          `evidence-cost-cache:${stage.stage_id}`,
        );
        if (entry.created_by_request_sha256 === canonicalRequestSha256) {
          cacheStatus = "miss";
          misses += 1;
        } else {
          cacheStatus = "persistent-hit";
          persistentHits += 1;
        }
      } else {
        const reopened = publishAndReopen(
          stateRoot,
          entryRef,
          candidate.bytes,
          `evidence-cost-cache:${stage.stage_id}`,
        );
        const entry = validateCacheEntry(
          reopened,
          identity,
          candidate.key,
          `evidence-cost-cache:${stage.stage_id}`,
        );
        if (entry.created_by_request_sha256 === canonicalRequestSha256) {
          cacheStatus = "miss";
          misses += 1;
        } else {
          cacheStatus = "persistent-hit";
          persistentHits += 1;
        }
      }
      seen.add(candidate.key);
      const boundEntry = readAnchoredArtifact(
        stateRoot,
        entryRef,
        MAX_EVIDENCE_BYTES,
        `evidence-cost-cache:${stage.stage_id}`,
      );
      if (!boundEntry)
        throw new Error(`evidence-cost-cache-reopen-missing:${stage.stage_id}`);
      unique.set(
        candidate.key,
        validateCacheEntry(
          boundEntry,
          identity,
          candidate.key,
          `evidence-cost-cache:${stage.stage_id}`,
        ),
      );
      observedWallMs += stage.cost.wall_ms;
      return {
        ...stage,
        cache_key: candidate.key,
        cache_status: cacheStatus,
        entry_ref: entryRef,
      };
    }),
  }));
  const entries = [...unique.values()].sort((left, right) =>
    left.cache_key.localeCompare(right.cache_key),
  );
  const uniqueWallMs = entries.reduce(
    (total, entry) => total + entry.stage.cost.wall_ms,
    0,
  );
  const missing = [
    ...new Set(entries.flatMap((entry) => missingAxes(entry.stage))),
  ].sort();
  const measurementTargets =
    missing.length > 0 ? missing : ["trusted-collector-binding"];
  const nextMode = {
    id: "evidence-cost/measure-missing-axes",
    prompt: `Intent-Slice: HARNESS-CROSS-CUTTING-EVIDENCE-COST-MEASUREMENT-COLLECTORS。量測 ${measurementTargets.join(", ")}；不得以 0 代替未量測值，並維持前景快速路徑不啟動 production 收集器。`,
  };
  const ledger = {
    schema_version: "repo-evidence-cost-ledger@v1",
    status: "projected",
    request_id: request.request_id,
    request_ref: requestRef,
    request_sha256: canonicalRequestSha256,
    observed_at: request.observed_at,
    cost_claim_boundary: "external-hash-bound-observation/unadmitted-collector",
    trace: { plan: request.plan, source_inputs: request.source_inputs },
    totals: {
      stage_uses: combinations.reduce(
        (total, combination) => total + combination.stages.length,
        0,
      ),
      unique_entries: entries.length,
      observed_wall_ms: observedWallMs,
      unique_wall_ms: uniqueWallMs,
      avoided_wall_ms: observedWallMs - uniqueWallMs,
      asserted_cpu_user_ms: entries.reduce(
        (total, entry) => total + numeric(entry.stage.cost.cpu.user_ms),
        0,
      ),
      asserted_cpu_system_ms: entries.reduce(
        (total, entry) => total + numeric(entry.stage.cost.cpu.system_ms),
        0,
      ),
      asserted_read_bytes: entries.reduce(
        (total, entry) => total + numeric(entry.stage.cost.io.read_bytes),
        0,
      ),
      asserted_write_bytes: entries.reduce(
        (total, entry) => total + numeric(entry.stage.cost.io.write_bytes),
        0,
      ),
      asserted_llm_input_tokens: entries.reduce(
        (total, entry) => total + numeric(entry.stage.cost.llm.input_tokens),
        0,
      ),
      asserted_llm_output_tokens: entries.reduce(
        (total, entry) => total + numeric(entry.stage.cost.llm.output_tokens),
        0,
      ),
    },
    cache: {
      unique_entries: entries.length,
      misses,
      persistent_hits: persistentHits,
      cross_combination_hits: crossCombinationHits,
    },
    combinations,
    missing_cost_axes: missing,
    next_mode: nextMode,
    activation: {
      cache_projection_enabled: true,
      workers_executed: false,
      background_admission_enabled: false,
      forgejo_enabled: false,
      cloud_enabled: false,
    },
    admission_eligible: false,
  };
  const ledgerBytes = Buffer.from(`${JSON.stringify(ledger, null, 2)}\n`);
  const ledgerRef = `ledgers/${request.request_id}.json`;
  const reopenedLedger = publishAndReopen(
    stateRoot,
    ledgerRef,
    ledgerBytes,
    "evidence-cost-ledger",
  );
  if (!reopenedLedger.equals(ledgerBytes))
    throw new Error("evidence-cost-ledger-publication-mismatch");
  return {
    schema_version: "repo-evidence-cost-cache-completion@v1",
    status: "projected",
    request_id: request.request_id,
    request_ref: requestRef,
    request_sha256: canonicalRequestSha256,
    ledger_ref: ledgerRef,
    ledger_sha256: sha256(ledgerBytes),
    cache: {
      unique_entries: entries.length,
      misses,
      persistent_hits: persistentHits,
      cross_combination_hits: crossCombinationHits,
    },
    admission_eligible: false,
  };
}
