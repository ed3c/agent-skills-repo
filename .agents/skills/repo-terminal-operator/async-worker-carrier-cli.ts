#!/usr/bin/env bun
import {
  executeAsyncWorker,
  readAsyncWorkerRequest,
} from "./async-worker-carrier";
import { persistAsyncWorkerProgress } from "./async-progress-store";

async function main(args: string[]): Promise<number> {
  const requestPath =
    args.length === 2 && args[0] === "--request" ? args[1] : null;
  const stateRoot = process.env.REPO_ASYNC_STATE_ROOT;
  const sourceRoot = process.env.REPO_ASYNC_SOURCE_ROOT;
  if (!requestPath || !stateRoot || !sourceRoot) {
    console.error(
      "usage: REPO_ASYNC_STATE_ROOT=<path> REPO_ASYNC_SOURCE_ROOT=<path> async-worker-carrier-cli.ts --request <path>",
    );
    return 64;
  }
  try {
    const completion = await executeAsyncWorker(
      readAsyncWorkerRequest(requestPath),
      stateRoot,
      sourceRoot,
      (event) => {
        persistAsyncWorkerProgress(stateRoot, event);
        process.stderr.write(`${JSON.stringify(event)}\n`);
      },
    );
    process.stdout.write(`${JSON.stringify(completion)}\n`);
    return completion.status === "verified" ? 0 : 2;
  } catch (error) {
    process.stderr.write(
      `${JSON.stringify({
        schema_version: "repo-async-production-worker-error@v1",
        status: "failed",
        diagnostic: error instanceof Error ? error.message : String(error),
      })}\n`,
    );
    return 2;
  }
}

process.exitCode = await main(process.argv.slice(2));
