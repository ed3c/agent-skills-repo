#!/usr/bin/env bun
import {
  closeSync,
  constants,
  fstatSync,
  openSync,
  readFileSync,
} from "node:fs";
import { isAbsolute, resolve } from "node:path";
import {
  cancelAsyncProductionRun,
  finishAsyncProductionRun,
  sealAsyncProductionRun,
  type AsyncFinishInput,
} from "./async-job-lifecycle";

function exactKeys(value: Record<string, unknown>, keys: string[]): void {
  if (
    JSON.stringify(Object.keys(value).sort()) !==
    JSON.stringify([...keys].sort())
  ) {
    throw new Error("request-fields-mismatch");
  }
}

function decodeCanonicalBase64(
  value: unknown,
  field: string,
  allowEmpty = false,
): Buffer {
  if (
    typeof value !== "string" ||
    !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(
      value,
    )
  ) {
    throw new Error(`invalid-base64:${field}`);
  }
  const bytes = Buffer.from(value, "base64");
  if (
    (!allowEmpty && bytes.length === 0) ||
    bytes.toString("base64") !== value
  ) {
    throw new Error(`non-canonical-base64:${field}`);
  }
  return bytes;
}

function readRequest(path: string): Record<string, unknown> {
  const descriptor = openSync(
    resolve(path),
    constants.O_RDONLY | constants.O_NOFOLLOW,
  );
  let bytes: Buffer | undefined;
  let failure: Error | undefined;
  try {
    const stat = fstatSync(descriptor);
    if (!stat.isFile() || stat.size > 1_048_576)
      throw new Error("unsafe-or-oversized-lifecycle-request");
    bytes = readFileSync(descriptor);
  } catch (error) {
    failure =
      error instanceof Error
        ? error
        : new Error("lifecycle request read failed", { cause: error });
  }
  try {
    closeSync(descriptor);
  } catch (error) {
    const cleanup =
      error instanceof Error
        ? error
        : new Error("lifecycle request cleanup failed", { cause: error });
    failure = failure
      ? new AggregateError(
          [failure, cleanup],
          "request read and cleanup failed",
          { cause: cleanup },
        )
      : cleanup;
  }
  if (failure) throw failure;
  if (!bytes) throw new Error("lifecycle request read produced no bytes");
  const value = JSON.parse(bytes.toString("utf8")) as unknown;
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("lifecycle-request-not-object");
  return value as Record<string, unknown>;
}

function diagnostic(error: unknown): string {
  const messages: string[] = [];
  let current = error;
  for (let depth = 0; depth < 8 && current instanceof Error; depth += 1) {
    messages.push(current.message);
    current = current.cause;
  }
  return messages.join(": ");
}

function seal(root: string, value: Record<string, unknown>): unknown {
  exactKeys(value, [
    "runId",
    "jobId",
    "targetRepo",
    "expectedHead",
    "deadlineAt",
    "candidateFiles",
    "foregroundReceiptBase64",
    "productionJobBase64",
  ]);
  if (!Array.isArray(value.candidateFiles))
    throw new Error("candidate-files-must-be-an-array");
  const candidateFiles = value.candidateFiles.map((candidate, index) => {
    if (
      !candidate ||
      typeof candidate !== "object" ||
      Array.isArray(candidate)
    ) {
      throw new Error(`invalid-candidate-file:${index}`);
    }
    const fields = candidate as Record<string, unknown>;
    exactKeys(fields, ["path", "contentBase64"]);
    return {
      path: String(fields.path),
      bytes: decodeCanonicalBase64(
        fields.contentBase64,
        `candidateFiles[${index}]`,
        true,
      ),
    };
  });
  return sealAsyncProductionRun(root, {
    runId: String(value.runId),
    jobId: String(value.jobId),
    targetRepo: String(value.targetRepo),
    expectedHead: String(value.expectedHead),
    deadlineAt: String(value.deadlineAt),
    candidateFiles,
    foregroundReceipt: decodeCanonicalBase64(
      value.foregroundReceiptBase64,
      "foregroundReceiptBase64",
    ),
    productionJob: decodeCanonicalBase64(
      value.productionJobBase64,
      "productionJobBase64",
    ),
  });
}

function transition(
  root: string,
  action: string,
  value: Record<string, unknown>,
): unknown {
  if (action === "--seal") return seal(root, value);
  if (action === "--cancel") {
    exactKeys(value, ["runId", "expectedVersion", "reason", "now"]);
    return cancelAsyncProductionRun(root, {
      runId: String(value.runId),
      expectedVersion: Number(value.expectedVersion),
      reason: String(value.reason),
      now: new Date(String(value.now)),
    });
  }
  if (action === "--finish") {
    exactKeys(value, [
      "runId",
      "expectedVersion",
      "fencingToken",
      "now",
      "terminalStatus",
      "resultRef",
      "resultSha256",
    ]);
    return finishAsyncProductionRun(root, {
      runId: String(value.runId),
      expectedVersion: Number(value.expectedVersion),
      fencingToken: String(value.fencingToken),
      now: new Date(String(value.now)),
      terminalStatus: String(
        value.terminalStatus,
      ) as AsyncFinishInput["terminalStatus"],
      resultRef: String(value.resultRef),
      resultSha256: String(value.resultSha256),
    });
  }
  throw new Error("unsupported-lifecycle-action");
}

function main(args: string[]): number {
  const root = process.env.REPO_ASYNC_STATE_ROOT;
  const [action, requestPath] = args;
  if (
    !root ||
    !isAbsolute(root) ||
    args.length !== 2 ||
    !action ||
    !requestPath
  ) {
    console.error(
      "usage: REPO_ASYNC_STATE_ROOT=<absolute> async-job-lifecycle-cli.ts --seal|--cancel|--finish <request.json>",
    );
    return 64;
  }
  try {
    const view = transition(root, action, readRequest(requestPath));
    console.log(
      JSON.stringify({
        schemaVersion: "repo-async-production-transition-receipt@v2",
        status: "passed",
        view,
      }),
    );
    return 0;
  } catch (error) {
    console.log(
      JSON.stringify({
        schemaVersion: "repo-async-production-transition-error@v2",
        status: "failed",
        diagnostic: diagnostic(error),
      }),
    );
    return 2;
  }
}

process.exitCode = main(process.argv.slice(2));
