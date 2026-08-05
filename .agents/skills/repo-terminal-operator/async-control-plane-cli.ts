#!/usr/bin/env bun
import {
  parseAsyncControlRequest,
  projectAsyncControl,
  readAsyncControlRequest,
} from "./async-control-plane";

function main(args: string[]): number {
  const requestPath =
    args.length === 2 && args[0] === "--request" ? args[1] : undefined;
  const stateRoot = process.env.REPO_ASYNC_STATE_ROOT;
  if (!requestPath || !stateRoot) {
    process.stderr.write(
      "usage: REPO_ASYNC_STATE_ROOT=<path> async-control-plane-cli.ts --request <path>\n",
    );
    return 64;
  }
  try {
    const request = parseAsyncControlRequest(
      readAsyncControlRequest(requestPath),
    );
    process.stdout.write(
      `${JSON.stringify(projectAsyncControl(stateRoot, request))}\n`,
    );
    return 0;
  } catch (error) {
    process.stderr.write(
      `${JSON.stringify({
        schema_version: "repo-async-production-control-error@v1",
        status: "failed",
        diagnostic: error instanceof Error ? error.message : String(error),
      })}\n`,
    );
    return 2;
  }
}

process.exitCode = main(process.argv.slice(2));
