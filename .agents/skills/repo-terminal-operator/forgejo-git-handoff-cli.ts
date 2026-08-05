#!/usr/bin/env bun
import { createHash } from "node:crypto";
import { readFileSync, realpathSync, statSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { runBoundedProcess } from "./bounded-subprocess";
import {
  executeForgejoGitHandoff,
  GitHandoffFailure,
  type ForgejoHandoffRequest,
} from "./forgejo-git-handoff";

type Packet = ForgejoHandoffRequest & {
  bindings: {
    bootstrap_request_ref: string;
    bootstrap_request_sha256: string;
    bootstrap_request_id: string;
    intent_classification:
      | "HARNESS-CROSS-CUTTING-FORGEJO-GIT-HANDOFF"
      | "HARNESS-CROSS-CUTTING-FORGEJO-PR-BRANCH-HANDOFF";
  };
  execution_policy: {
    automatic_execution: false;
    network_scope: "loopback-only";
    cloud_enabled: false;
    github_enabled: false;
  };
  status: "ready_for_repo_local_operator";
  next_mode:
    | "repo-local/execute-forgejo-handoff"
    | "repo-local/execute-forgejo-pr-branch-handoff";
};

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function contained(root: string, path: string): boolean {
  const local = relative(root, path);
  return !isAbsolute(local) && local !== ".." && !local.startsWith(`..${sep}`);
}

function readPacket(path: string): { bytes: Buffer; packet: Packet } {
  const stat = statSync(path);
  if (!stat.isFile() || stat.size > 64 * 1024)
    throw new GitHandoffFailure("invalid-request", "request is not bounded");
  const bytes = readFileSync(path);
  return { bytes, packet: JSON.parse(bytes.toString("utf8")) as Packet };
}

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  if (
    args.length !== 4 ||
    args[0] !== "--workspace-root" ||
    args[2] !== "--request"
  )
    throw new GitHandoffFailure(
      "usage",
      "expected --workspace-root <root> --request <packet>",
    );
  const workspaceRoot = realpathSync(args[1] ?? "");
  const requestPath = realpathSync(resolve(workspaceRoot, args[3] ?? ""));
  if (!contained(workspaceRoot, requestPath))
    throw new GitHandoffFailure("request-escape", "request escapes workspace");
  const { bytes, packet } = readPacket(requestPath);
  const schema =
    packet.schema_version === "forgejo-git-handoff-request@v1"
      ? packet.schema_version
      : packet.schema_version === "forgejo-pr-branch-handoff-request@v1"
        ? packet.schema_version
        : null;
  if (!schema)
    throw new GitHandoffFailure(
      "unsupported-request",
      "request schema is not an admitted Git handoff contract",
    );
  const bun = Bun.which("bun");
  if (!bun) throw new GitHandoffFailure("bun-missing", "bun is unavailable");
  const validation = await runBoundedProcess(
    [
      bun,
      "run",
      resolve(workspaceRoot, "runtime/contracts/validate-packet.ts"),
      "--schema",
      schema,
      "--input",
      requestPath,
    ],
    { cwd: workspaceRoot, timeoutMs: 10_000 },
  );
  if (
    validation.exitCode !== 0 ||
    validation.timedOut ||
    validation.cancelled ||
    validation.cleanupErrors.length > 0
  )
    throw new GitHandoffFailure(
      "schema-validation-failed",
      (validation.stdout || validation.stderr).trim().slice(0, 1000),
    );
  const bootstrapPath = realpathSync(
    resolve(workspaceRoot, packet.bindings.bootstrap_request_ref),
  );
  if (!contained(workspaceRoot, bootstrapPath))
    throw new GitHandoffFailure(
      "bootstrap-escape",
      "bootstrap request escapes workspace",
    );
  const bootstrapBytes = readFileSync(bootstrapPath);
  if (sha256(bootstrapBytes) !== packet.bindings.bootstrap_request_sha256)
    throw new GitHandoffFailure(
      "bootstrap-hash-mismatch",
      "bootstrap request bytes changed",
    );
  const bootstrap = JSON.parse(bootstrapBytes.toString("utf8")) as {
    schema_version?: string;
    request_id?: string;
    target?: { repository_full_name?: string };
  };
  if (
    bootstrap.schema_version !== "forgejo-repository-bootstrap-request@v1" ||
    bootstrap.request_id !== packet.bindings.bootstrap_request_id ||
    bootstrap.target?.repository_full_name !==
      packet.target.repository_full_name
  )
    throw new GitHandoffFailure(
      "bootstrap-binding-mismatch",
      "bootstrap identity differs from Git handoff",
    );
  const receipt = await executeForgejoGitHandoff(packet, { workspaceRoot });
  console.log(
    JSON.stringify({
      ...receipt,
      request_ref: relative(workspaceRoot, requestPath),
      request_sha256: sha256(bytes),
      cloud_enabled: false,
      github_enabled: false,
    }),
  );
}

try {
  await main();
} catch (error) {
  console.log(
    JSON.stringify({
      schema_version: "forgejo-git-handoff-error@v1",
      status: "failed",
      reason:
        error instanceof GitHandoffFailure ? error.reason : "internal-error",
      detail: error instanceof Error ? error.message : "handoff failed",
      cloud_enabled: false,
    }),
  );
  process.exitCode = 2;
}
