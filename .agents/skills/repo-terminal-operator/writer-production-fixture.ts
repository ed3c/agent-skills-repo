import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

export type WriterJourneyPaths = {
  root: string;
  candidate: string;
  conflictCandidate: string;
  missingCandidate: string;
  raceOutput: string;
  rollbackOutput: string;
  cancellationOutput: string;
  sourceSha256: string;
  claimSetSha256: string;
  outputSha256: string;
};

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

export function writeWriterJourneyFixtures(artifactRoot: string, workspaceRoot: string): WriterJourneyPaths {
  const sourcePath = process.env.SKILL_BETTOR_SOURCE_MESSAGE_PATH;
  if (!sourcePath) throw new Error("writer journey requires the carrier-owned source message");
  const source = readFileSync(sourcePath);
  const claimSetPath = resolve(workspaceRoot, "guided/user-production-safety-20260731.claim-set.json");
  const claimSet = readFileSync(claimSetPath);
  const parsed = JSON.parse(claimSet.toString("utf8")) as { source_sha256?: string };
  const sourceSha256 = sha256(source);
  if (parsed.source_sha256 !== sourceSha256) throw new Error("writer journey source and claim-set identity differ");

  const root = join(artifactRoot, "writer-workspace");
  for (const name of ["race", "rollback", "cancellation"]) mkdirSync(join(root, name), { recursive: true });
  const candidate = join(root, "candidate.json");
  const conflictCandidate = join(root, "conflict-candidate.json");
  const candidateBytes = Buffer.from(`${JSON.stringify({
    schema_version: "repo-terminal-source-bound-artifact@v1",
    source_sha256: sourceSha256,
    claim_set_sha256: sha256(claimSet),
  })}\n`);
  writeFileSync(candidate, candidateBytes, { flag: "wx", mode: 0o600 });
  writeFileSync(conflictCandidate, Buffer.from("conflicting writer candidate\n"), { flag: "wx", mode: 0o600 });
  return {
    root, candidate, conflictCandidate, missingCandidate: join(root, "missing-candidate.json"),
    raceOutput: "race/result.json", rollbackOutput: "rollback/result.json",
    cancellationOutput: "cancellation/result.json", sourceSha256,
    claimSetSha256: sha256(claimSet), outputSha256: sha256(candidateBytes),
  };
}
