#!/usr/bin/env bun
import {
  parseEvidenceCostCacheRequest,
  projectEvidenceCostCache,
  readEvidenceCostCacheRequest,
} from "./evidence-cost-cache";

function main(args: string[]): number {
  const requestPath =
    args.length === 2 && args[0] === "--request" ? args[1] : undefined;
  const stateRoot = process.env.REPO_EVIDENCE_STATE_ROOT;
  if (!requestPath || !stateRoot) {
    process.stderr.write(
      "usage: REPO_EVIDENCE_STATE_ROOT=<path> evidence-cost-cache-cli.ts --request <path>\n",
    );
    return 64;
  }
  try {
    const request = parseEvidenceCostCacheRequest(
      readEvidenceCostCacheRequest(requestPath),
    );
    process.stdout.write(
      `${JSON.stringify(projectEvidenceCostCache(stateRoot, request))}\n`,
    );
    return 0;
  } catch (error) {
    process.stderr.write(
      `${JSON.stringify({
        schema_version: "repo-evidence-cost-cache-error@v1",
        status: "failed",
        diagnostic: error instanceof Error ? error.message : String(error),
      })}\n`,
    );
    return 2;
  }
}

process.exitCode = main(process.argv.slice(2));
