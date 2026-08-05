import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { validateProductionArtifact } from "../../../../../skills/repo-neural-perception/scripts/production-artifact-verifier";
import { verifiedProductionSafety } from "../../../../../skills/repo-neural-perception/scripts/production-receipt-verifier";
import type { ProfileSummary } from "../../../../../skills/repo-neural-perception/scripts/profile-runner";
import type { BoundedProcessResult } from "./bounded-subprocess";
import {
  REPO_TERMINAL_CODE_QUALITY_COMMAND,
  taskQualityReceiptErrors,
} from "./task-quality-contract";

export { REPO_TERMINAL_CODE_QUALITY_COMMAND } from "./task-quality-contract";

export type GateProfile = {
  schema_version: "repo-gate-profile@v1";
  command_timeout_ms: number;
  commands: string[][];
};

export function loadGateProfile(
  root: string,
  name: string,
  declared: string[][],
): GateProfile {
  const parsed = JSON.parse(
    readFileSync(resolve(root, name), "utf8"),
  ) as GateProfile;
  if (
    parsed.schema_version !== "repo-gate-profile@v1" ||
    parsed.commands.length !== 1 ||
    parsed.commands[0]?.length === 0
  ) {
    throw new Error(`${name} must contain exactly one physical argv command`);
  }
  if (JSON.stringify(parsed.commands) !== JSON.stringify(declared)) {
    throw new Error(`${name} command drift from terminal packet`);
  }
  return parsed;
}

function receipt(result: BoundedProcessResult): Record<string, unknown> | null {
  const line = result.stdout.trim().split(/\r?\n/u).filter(Boolean).at(-1);
  if (!line) return null;
  try {
    const parsed = JSON.parse(line) as unknown;
    return parsed !== null &&
      typeof parsed === "object" &&
      !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function codeQualityErrors(value: Record<string, unknown>): string[] {
  const expected: Record<string, unknown> = {
    exit_code: 0,
    timed_out: false,
    cancelled: false,
    process_reaped: true,
    timer_cleared: true,
    stdout_consumed: true,
    stderr_consumed: true,
  };
  const failures = Object.entries(expected)
    .filter(([field, expectedValue]) => value[field] !== expectedValue)
    .map(([field]) => `inner ${field}`);
  if (!Array.isArray(value.cleanup_errors) || value.cleanup_errors.length !== 0)
    failures.push("inner cleanup_errors");
  if (
    JSON.stringify(value.command) !==
    JSON.stringify(REPO_TERMINAL_CODE_QUALITY_COMMAND)
  ) {
    failures.push("inner command");
  }
  failures.push(...taskQualityReceiptErrors(value));
  return failures;
}

function productionErrors(
  value: Record<string, unknown>,
  workspaceRoot: string,
): string[] {
  const errors: string[] = [];
  const safety = verifiedProductionSafety(value, "writer-entrypoint").sort();
  const expectedSafety = ["race-condition", "resource-leak", "silent-failure"];
  if (JSON.stringify(safety) !== JSON.stringify(expectedSafety))
    errors.push("production safety evidence incomplete");
  const summary: ProfileSummary = {
    declared: 1,
    executed: 1,
    artifact:
      typeof value.artifact_path === "string" ? value.artifact_path : null,
    artifactSha256:
      typeof value.artifact_sha256 === "string" ? value.artifact_sha256 : null,
    safety,
    evidenceScope:
      typeof value.evidence_scope === "string" ? value.evidence_scope : null,
    requiredEvidenceScope: "writer-entrypoint",
  };
  validateProductionArtifact(workspaceRoot, summary, errors);
  return errors;
}

export function gateReceiptErrors(
  name: "code-quality" | "production-use",
  result: BoundedProcessResult,
  workspaceRoot: string,
): string[] {
  const value = receipt(result);
  const schema =
    name === "code-quality"
      ? "repo-terminal-code-quality-receipt@v1"
      : "repo-terminal-writer-production-journey-receipt@v2";
  if (!value || value.schema_version !== schema || value.status !== "passed")
    return [`typed receipt must pass ${schema}`];
  return name === "code-quality"
    ? codeQualityErrors(value)
    : productionErrors(value, workspaceRoot);
}
