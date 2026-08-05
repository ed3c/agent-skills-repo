#!/usr/bin/env bun
import { readAnchoredArtifact } from "./anchored-artifact-read";
import {
  executeAsyncProductionFacade,
  parseAsyncFacadeRequest,
} from "./async-admission-facade";

const MAX_REQUEST_BYTES = 64 * 1024;

function main(args: string[]): number {
  const requestRef =
    args.length === 2 && args[0] === "--request" ? args[1] : undefined;
  const stateRoot = process.env.REPO_ASYNC_STATE_ROOT;
  const sourceRoot = process.env.REPO_ASYNC_SOURCE_ROOT;
  if (!requestRef || !stateRoot || !sourceRoot) {
    process.stderr.write(
      "usage: REPO_ASYNC_STATE_ROOT=<path> REPO_ASYNC_SOURCE_ROOT=<path> async-admission-facade-cli.ts --request <repo-relative-ref>\n",
    );
    return 64;
  }
  try {
    const bytes = readAnchoredArtifact(
      sourceRoot,
      requestRef,
      MAX_REQUEST_BYTES,
      "facade-request",
    );
    if (!bytes) throw new Error("facade-request-missing");
    const completion = executeAsyncProductionFacade(
      stateRoot,
      sourceRoot,
      parseAsyncFacadeRequest(bytes),
    );
    process.stdout.write(`${JSON.stringify(completion)}\n`);
    return 0;
  } catch (error) {
    process.stderr.write(
      `${JSON.stringify({
        schema_version: "repo-async-production-facade-error@v1",
        status: "failed",
        diagnostic: error instanceof Error ? error.message : String(error),
        admission_eligible: false,
      })}\n`,
    );
    return 2;
  }
}

process.exitCode = main(process.argv.slice(2));
