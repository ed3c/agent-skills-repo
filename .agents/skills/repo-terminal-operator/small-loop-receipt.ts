import { resolve } from "node:path";
import type { BoundedProcessResult } from "./bounded-subprocess";
import type { PreflightCheck } from "./repo-preflight";

export type GateStatus = "passed" | "failed" | "blocked";
export type Stage = {
  name: string;
  ecosystem: "bun";
  purpose: "runtime" | "code-quality" | "production-use";
  status: GateStatus | "cached";
  attempts: number;
  inclusive_ms: number;
  exclusive_ms: number;
  cache: "miss" | "not-applicable";
};
export type Failure = {
  stage: string;
  kind: "deterministic" | "transient" | "policy" | "stale";
  message: string;
};
export type TerminalPacket = {
  terminal_slice_id: string;
  allowed_paths: string[];
  write_lease: { expected_head: string; expires_at: string };
  code_quality: { commands: string[][] };
  production_use: { commands: string[][] };
  handoff: { run_id: string };
};
export type StageEvidence = { command: string[]; result: BoundedProcessResult };
export type GateExecution = {
  stages: [Stage, Stage];
  failures: Failure[];
  codeQualityEvidence: StageEvidence | null;
  productionUseEvidence: StageEvidence | null;
};
export type SmallLoopReceipt = {
  schema_version: "small-loop-run-receipt@v1";
  run_id: string;
  terminal_slice_id: string;
  mode: "confidence";
  status: "passed" | "failed" | "stale";
  started_at: string;
  completed_at: string;
  expected_head: string;
  actual_head: string;
  changed_paths: string[];
  stages: Stage[];
  code_quality: { status: GateStatus; receipt_ref: string };
  production_use: { status: GateStatus; receipt_ref: string };
  failures: Failure[];
  next_action: {
    kind: "open-pr" | "repair" | "retry-transient";
    prompt_packet_ref: string;
  };
  handoff: {
    input: TerminalPacket["handoff"];
    preflight: PreflightCheck[];
    stage_receipts: {
      code_quality: StageEvidence | null;
      production_use: StageEvidence | null;
    };
  };
};
export type ReceiptInput = {
  inputPath: string;
  packet: TerminalPacket;
  startedAt: Date;
  expectedHead: string;
  actualHead: string;
  changedPaths: string[];
  preflight: PreflightCheck[];
  preflightElapsed: number;
  gates: GateExecution;
  failures: Failure[];
  completedAt: Date;
};

const RECEIPT_KEYS = [
  "schema_version",
  "run_id",
  "terminal_slice_id",
  "mode",
  "status",
  "started_at",
  "completed_at",
  "expected_head",
  "actual_head",
  "changed_paths",
  "stages",
  "code_quality",
  "production_use",
  "failures",
  "next_action",
  "handoff",
] as const;
const STAGE_KEYS = [
  "name",
  "ecosystem",
  "purpose",
  "status",
  "attempts",
  "inclusive_ms",
  "exclusive_ms",
  "cache",
] as const;

function object(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function exactKeys(
  value: Record<string, unknown> | undefined,
  keys: readonly string[],
): boolean {
  return (
    Boolean(value) &&
    Object.keys(value ?? {}).length === keys.length &&
    keys.every((key) => Object.hasOwn(value ?? {}, key))
  );
}

function nonempty(value: unknown): value is string {
  return typeof value === "string" && value.length > 0;
}

function validDate(value: unknown): boolean {
  return nonempty(value) && Number.isFinite(Date.parse(value));
}

function validStage(value: unknown): boolean {
  const stage = object(value);
  return (
    exactKeys(stage, STAGE_KEYS) &&
    nonempty(stage?.name) &&
    ["bun", "python", "browser", "agy", "forgejo", "mixed"].includes(
      String(stage.ecosystem),
    ) &&
    [
      "runtime",
      "migration",
      "parity",
      "code-quality",
      "production-use",
      "evidence",
      "release",
    ].includes(String(stage.purpose)) &&
    ["passed", "failed", "blocked", "cached"].includes(String(stage.status)) &&
    Number.isSafeInteger(stage.attempts) &&
    Number(stage.attempts) >= 0 &&
    typeof stage.inclusive_ms === "number" &&
    stage.inclusive_ms >= 0 &&
    typeof stage.exclusive_ms === "number" &&
    stage.exclusive_ms >= 0 &&
    ["hit", "miss", "stored-failure", "not-applicable"].includes(
      String(stage.cache),
    )
  );
}

function validPassingGate(value: unknown): boolean {
  const gate = object(value);
  return (
    exactKeys(gate, ["status", "receipt_ref"]) &&
    gate?.status === "passed" &&
    nonempty(gate.receipt_ref)
  );
}

export function assertPassingSmallLoopReceipt(
  receipt: Record<string, unknown>,
  runId: string,
  expectedHead: string,
): void {
  const stages = Array.isArray(receipt.stages) ? receipt.stages : [];
  const changedPaths = Array.isArray(receipt.changed_paths)
    ? receipt.changed_paths
    : [];
  const nextAction = object(receipt.next_action);
  const valid =
    exactKeys(receipt, RECEIPT_KEYS) &&
    receipt.schema_version === "small-loop-run-receipt@v1" &&
    receipt.run_id === runId &&
    nonempty(receipt.terminal_slice_id) &&
    [
      "instant",
      "focused",
      "confidence",
      "migration",
      "parity",
      "audit",
      "release",
    ].includes(String(receipt.mode)) &&
    receipt.status === "passed" &&
    validDate(receipt.started_at) &&
    validDate(receipt.completed_at) &&
    receipt.expected_head === expectedHead &&
    receipt.actual_head === expectedHead &&
    /^[a-f0-9]{40}$/u.test(expectedHead) &&
    changedPaths.every(nonempty) &&
    stages.length > 0 &&
    stages.every(validStage) &&
    stages.some(
      (stage) =>
        object(stage)?.purpose === "code-quality" &&
        object(stage)?.status === "passed",
    ) &&
    stages.some(
      (stage) =>
        object(stage)?.purpose === "production-use" &&
        object(stage)?.status === "passed",
    ) &&
    validPassingGate(receipt.code_quality) &&
    validPassingGate(receipt.production_use) &&
    Array.isArray(receipt.failures) &&
    receipt.failures.length === 0 &&
    exactKeys(nextAction, ["kind", "prompt_packet_ref"]) &&
    nextAction?.kind === "open-pr" &&
    nonempty(nextAction.prompt_packet_ref) &&
    Boolean(object(receipt.handoff));
  if (!valid) throw new Error("passing-small-loop-receipt-invalid");
}

export function preflightStage(elapsed: number, passed: boolean): Stage {
  return {
    name: "preflight",
    ecosystem: "bun",
    purpose: "runtime",
    status: passed ? "passed" : "failed",
    attempts: 1,
    inclusive_ms: elapsed,
    exclusive_ms: elapsed,
    cache: "not-applicable",
  };
}

export function preflightFailures(checks: PreflightCheck[]): Failure[] {
  const failed = checks.filter((check) => check.status === "failed");
  return failed.length === 0
    ? []
    : [
        {
          stage: "preflight",
          kind: "policy",
          message: failed
            .map((check) => `${check.id}: ${check.detail}`)
            .join("; "),
        },
      ];
}

function gateEvidence(
  stage: Stage,
  receiptRef: string,
): SmallLoopReceipt["code_quality"] {
  const status: GateStatus =
    stage.status === "passed"
      ? "passed"
      : stage.status === "failed"
        ? "failed"
        : "blocked";
  return { status, receipt_ref: receiptRef };
}

function receiptStatus(input: ReceiptInput): SmallLoopReceipt["status"] {
  if (input.actualHead !== input.expectedHead) return "stale";
  return input.failures.length === 0 ? "passed" : "failed";
}

function nextAction(
  status: SmallLoopReceipt["status"],
  failures: Failure[],
): SmallLoopReceipt["next_action"]["kind"] {
  if (status === "passed") return "open-pr";
  return failures.some((failure) => failure.kind === "transient")
    ? "retry-transient"
    : "repair";
}

export function buildReceipt(input: ReceiptInput): SmallLoopReceipt {
  const status = receiptStatus(input);
  const stages = [
    preflightStage(
      input.preflightElapsed,
      input.preflight.every((check) => check.status === "passed"),
    ),
    ...input.gates.stages,
  ];
  return {
    schema_version: "small-loop-run-receipt@v1",
    run_id: input.packet.handoff.run_id,
    terminal_slice_id: input.packet.terminal_slice_id,
    mode: "confidence",
    status,
    started_at: input.startedAt.toISOString(),
    completed_at: input.completedAt.toISOString(),
    expected_head: input.expectedHead,
    actual_head: input.actualHead,
    changed_paths: input.changedPaths,
    stages,
    code_quality: gateEvidence(
      input.gates.stages[0],
      "inline://handoff/stage_receipts/code_quality",
    ),
    production_use: gateEvidence(
      input.gates.stages[1],
      "inline://handoff/stage_receipts/production_use",
    ),
    failures: input.failures,
    next_action: {
      kind: nextAction(status, input.failures),
      prompt_packet_ref: resolve(input.inputPath),
    },
    handoff: {
      input: input.packet.handoff,
      preflight: input.preflight,
      stage_receipts: {
        code_quality: input.gates.codeQualityEvidence,
        production_use: input.gates.productionUseEvidence,
      },
    },
  };
}
