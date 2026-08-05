#!/usr/bin/env bun
import { resolve } from "node:path";
import {
  collectEvidenceCost,
  persistEvidenceCostPreconditionFailure,
} from "./evidence-cost-collector";

const ACTIVATION_ENV = "REPO_EVIDENCE_COLLECTOR_PRODUCTION";
const NEXT_INTENT =
  "HARNESS-CROSS-CUTTING-EVIDENCE-COST-MEASUREMENT-COLLECTORS";

function writeJson(
  stream: typeof process.stdout | typeof process.stderr,
  value: unknown,
): void {
  stream.write(`${JSON.stringify(value)}\n`);
}

async function main(argv: readonly string[]): Promise<number> {
  if (argv.length === 1 && argv[0] === "--describe") {
    writeJson(process.stdout, {
      schema_version: "repo-evidence-cost-collector-description@v1",
      status: "passed",
      mode: "production-only",
      activation_env: `${ACTIVATION_ENV}=1`,
      measured_axes: ["wall", "cpu-direct-child"],
      unavailable_axes: ["io-bytes", "llm-tokens"],
      foreground_execution_enabled: false,
      admission_eligible: false,
      next_intent: NEXT_INTENT,
    });
    return 0;
  }

  if (process.env[ACTIVATION_ENV] !== "1") {
    writeJson(process.stderr, {
      schema_version: "repo-evidence-cost-collector-error@v1",
      status: "failed",
      failure_kind: "activation-disabled",
      diagnostic:
        "trusted evidence-cost collection requires REPO_EVIDENCE_COLLECTOR_PRODUCTION=1",
      admission_eligible: false,
    });
    return 2;
  }

  const requestPath =
    argv.length === 2 && argv[0] === "--request" ? argv[1] : undefined;
  const stateRoot = process.env.REPO_EVIDENCE_STATE_ROOT;
  const repoRoot = process.env.REPO_EVIDENCE_COLLECTOR_REPO_ROOT;
  if (!requestPath || !stateRoot || !repoRoot) {
    writeJson(process.stderr, {
      schema_version: "repo-evidence-cost-collector-error@v1",
      status: "failed",
      failure_kind: "invalid-arguments",
      diagnostic:
        "expected state/repo roots and --request <collector-request.json>",
      admission_eligible: false,
    });
    return 2;
  }
  const controller = new AbortController();
  const cancel = () => controller.abort();
  let cancellationArmed = true;
  const disarmCancellation = () => {
    if (!cancellationArmed) return;
    process.removeListener("SIGINT", cancel);
    process.removeListener("SIGTERM", cancel);
    cancellationArmed = false;
  };
  process.once("SIGINT", cancel);
  process.once("SIGTERM", cancel);
  try {
    const completion = await collectEvidenceCost(
      resolve(stateRoot),
      resolve(repoRoot),
      resolve(requestPath),
      { signal: controller.signal, beforeCommit: disarmCancellation },
    );
    writeJson(
      completion.status === "measured" ? process.stdout : process.stderr,
      completion,
    );
    return completion.status === "measured" ? 0 : 2;
  } catch (error) {
    let failureReceipt: { ref: string; sha256: string } | null = null;
    let failureReceiptDiagnostic: string | null = null;
    try {
      failureReceipt = await persistEvidenceCostPreconditionFailure(
        resolve(stateRoot),
        resolve(repoRoot),
        resolve(requestPath),
        error,
        controller.signal,
      );
    } catch (publicationError) {
      failureReceiptDiagnostic =
        publicationError instanceof Error
          ? publicationError.message
          : String(publicationError);
    }
    writeJson(process.stderr, {
      schema_version: "repo-evidence-cost-collector-error@v1",
      status: "failed",
      failure_kind: "precondition-or-system",
      diagnostic: error instanceof Error ? error.message : String(error),
      failure_receipt: failureReceipt,
      failure_receipt_diagnostic: failureReceiptDiagnostic,
      admission_eligible: false,
    });
    return 2;
  } finally {
    disarmCancellation();
  }
}

process.exitCode = await main(process.argv.slice(2));
