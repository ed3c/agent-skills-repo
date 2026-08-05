#!/usr/bin/env bun
import { createHash } from "node:crypto";
import {
  closeSync,
  constants,
  fstatSync,
  openSync,
  readFileSync,
  realpathSync,
} from "node:fs";
import { dirname, isAbsolute, relative, resolve, sep } from "node:path";
import { WriterPublicationFailure } from "./writer-publication-lifecycle";
import { publishWriterArtifact } from "./writer-publication";

type WriterArgs = {
  root: string;
  output: string;
  candidate: string;
  artifactId: string;
  runId: string;
};

class WriterFailure extends Error {
  constructor(
    readonly stage: string,
    readonly reason: string,
    cause: unknown,
  ) {
    super(`${stage}: ${reason}`, { cause });
  }
}

function required(value: string | undefined, name: string): string {
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function parseArgs(argv: string[]): WriterArgs {
  const values = new Map<string, string>();
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index];
    const value = argv[index + 1];
    if (!flag?.startsWith("--") || value === undefined)
      throw new Error(`invalid argument at index ${index}`);
    if (values.has(flag)) throw new Error(`duplicate argument ${flag}`);
    values.set(flag, value);
  }
  return {
    root: required(values.get("--root"), "--root"),
    output: required(values.get("--output"), "--output"),
    candidate: required(values.get("--candidate"), "--candidate"),
    artifactId: required(values.get("--artifact-id"), "--artifact-id"),
    runId: required(values.get("--run-id"), "--run-id"),
  };
}

function contained(root: string, candidate: string): boolean {
  const local = relative(root, candidate);
  return (
    local !== "" &&
    !isAbsolute(local) &&
    local !== ".." &&
    !local.startsWith(`..${sep}`)
  );
}

function validateId(value: string, label: string): string {
  if (!/^[a-z0-9][a-z0-9._-]{2,127}$/u.test(value))
    throw new Error(`${label} is invalid`);
  return value;
}

function paths(args: WriterArgs): {
  root: string;
  output: string;
  candidate: string;
} {
  const root = realpathSync(args.root);
  const output = resolve(root, args.output);
  const candidate = realpathSync(args.candidate);
  if (isAbsolute(args.output) || !contained(root, output))
    throw new Error("--output must stay inside --root");
  if (!contained(root, candidate))
    throw new Error("--candidate must stay inside --root");
  if (realpathSync(dirname(output)) !== dirname(output))
    throw new Error("--output parent must not resolve through a symlink");
  return { root, output, candidate };
}

function readCandidate(path: string): Buffer {
  const descriptor = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  let primary: unknown;
  try {
    if (!fstatSync(descriptor).isFile())
      throw new Error("candidate must be a regular file");
    return readFileSync(descriptor);
  } catch (error) {
    primary = error;
    throw error;
  } finally {
    try {
      closeSync(descriptor);
    } catch (error) {
      throw new AggregateError(
        primary === undefined ? [error] : [primary, error],
        "candidate descriptor cleanup failed",
      );
    }
  }
}

function diagnostic(error: unknown): string {
  if (error instanceof AggregateError)
    return `${error.message}: ${error.errors.map(diagnostic).join("; ")}`;
  if (error instanceof Error && error.cause)
    return `${error.message}; cause=${diagnostic(error.cause)}`;
  return error instanceof Error ? error.message : String(error);
}

function failureKind(detail: string): string {
  return /failure_kind=([a-z-]+)/u.exec(detail)?.[1] ?? "system";
}

function execute(args: WriterArgs): Record<string, unknown> {
  const artifactId = validateId(args.artifactId, "--artifact-id");
  const runId = validateId(args.runId, "--run-id");
  const safe = paths(args);
  const candidate = readCandidate(safe.candidate);
  const publication = publishWriterArtifact(safe.root, safe.output, candidate);
  return {
    schema_version: "repo-terminal-writer-receipt@v1",
    status: "passed",
    writer_outcome: publication.writerOutcome,
    recovery_outcome: publication.recoveryOutcome,
    artifact_id: artifactId,
    run_id: runId,
    output_sha256: createHash("sha256").update(candidate).digest("hex"),
    artifact_created: publication.writerOutcome === "published",
  };
}

function main(argv: string[]): number {
  let args: WriterArgs | undefined;
  try {
    args = parseArgs(argv);
    console.log(JSON.stringify(execute(args)));
    return 0;
  } catch (error) {
    const failure = new WriterFailure(
      "publication",
      "writer entrypoint failed",
      error,
    );
    const detail = diagnostic(failure);
    const kind = failureKind(detail);
    console.error(
      JSON.stringify({
        schema_version: "repo-terminal-writer-failure@v1",
        status: "failed",
        stage: failure.stage,
        reason: failure.reason,
        failure_kind: kind,
        typed_failure_receipt: true,
        original_error_preserved: true,
        diagnostic: detail.slice(0, 2048),
        artifact_created:
          error instanceof WriterPublicationFailure && error.artifactCreated,
        artifact_id: args?.artifactId ?? null,
        run_id: args?.runId ?? null,
      }),
    );
    return kind === "conflict" ? 1 : 2;
  }
}

if (import.meta.main) process.exitCode = main(process.argv.slice(2));
