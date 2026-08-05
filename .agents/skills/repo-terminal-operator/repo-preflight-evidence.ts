import { createHash } from "node:crypto";
import { readFileSync, realpathSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";

type PreflightCheck = { id: string; status: "passed" | "failed"; detail: string };

type EvidencePacket = {
  schema_version: "terminal-slice-packet@v1" | "terminal-slice-packet@v2";
  terminal_slice_id?: string;
  guided_claim_ids: string[];
  claim_set?: { ref: string; sha256: string };
  agentic_execution?: {
    tdd: { red_receipt: string; green_receipt: string; tests_immutable_during_green: boolean };
  };
};

type TddReceipt = {
  terminal_slice_id?: string;
  red?: { status?: string; test_files_changed_during_green?: boolean };
  green?: { status?: string };
  minimal_diff?: { allowed_paths_only?: boolean; unrelated_refactors?: boolean };
};

function result(id: string, valid: boolean, detail: string): PreflightCheck {
  return { id, status: valid ? "passed" : "failed", detail };
}

function contained(base: string, candidate: string): boolean {
  const local = relative(base, candidate);
  return !isAbsolute(local) && local !== ".." && !local.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`);
}

function exactClaimIds(bytes: Buffer, expected: string[]): boolean {
  const claimSet = JSON.parse(bytes.toString("utf8")) as { claims: Array<{ claim_id: string }> };
  return JSON.stringify(claimSet.claims.map((claim) => claim.claim_id)) === JSON.stringify(expected);
}

function validClaimSet(actualSha: string, expectedSha: string, exitStatus: number | null, idsMatch: boolean): boolean {
  return actualSha === expectedSha && exitStatus === 0 && idsMatch;
}

export function claimSetCheck(packet: EvidencePacket, workspaceRoot: string): PreflightCheck {
  if (packet.schema_version !== "terminal-slice-packet@v2" || !packet.claim_set) return result("claim-set", true, "not required by v1");
  try {
    const claimSetPath = resolve(workspaceRoot, packet.claim_set.ref);
    if (!contained(workspaceRoot, claimSetPath)) throw new Error("claim-set path escapes workspace");
    if (!contained(realpathSync(workspaceRoot), realpathSync(claimSetPath))) throw new Error("claim-set realpath escapes workspace");
    const bytes = readFileSync(claimSetPath);
    const actualSha = createHash("sha256").update(bytes).digest("hex");
    const validation = spawnSync("bun", ["run", resolve(workspaceRoot, "runtime/claims/validate-claim-set.ts"), "--input", packet.claim_set.ref], {
      cwd: workspaceRoot, encoding: "utf8",
    });
    const valid = validClaimSet(actualSha, packet.claim_set.sha256, validation.status, exactClaimIds(bytes, packet.guided_claim_ids));
    return result("claim-set", valid, validation.stdout.trim() || validation.stderr.trim() || actualSha);
  } catch (error) {
    return result("claim-set", false, error instanceof Error ? error.message : String(error));
  }
}

function validTddReceipt(receipt: TddReceipt, packet: EvidencePacket, paths: string[]): boolean {
  return receipt.terminal_slice_id === packet.terminal_slice_id
    && receipt.red?.status === "failed-as-expected" && receipt.red.test_files_changed_during_green === false
    && receipt.green?.status === "passed" && receipt.minimal_diff?.allowed_paths_only === true
    && receipt.minimal_diff.unrelated_refactors === false && paths[0] === paths[1]
    && packet.agentic_execution?.tdd.tests_immutable_during_green === true;
}

export function tddReceiptCheck(packet: EvidencePacket, workspaceRoot: string): PreflightCheck {
  if (packet.schema_version !== "terminal-slice-packet@v2" || !packet.agentic_execution) return result("agentic-tdd-receipt", true, "not required by v1");
  const refs = [packet.agentic_execution.tdd.red_receipt, packet.agentic_execution.tdd.green_receipt];
  try {
    const paths = refs.map((ref) => resolve(workspaceRoot, ref.split("#", 1)[0] ?? ""));
    for (const path of paths) {
      if (!contained(workspaceRoot, path)) throw new Error("TDD receipt path escapes workspace");
      if (!contained(realpathSync(workspaceRoot), realpathSync(path))) throw new Error("TDD receipt realpath escapes workspace");
    }
    const receipt = JSON.parse(readFileSync(paths[0] ?? "", "utf8")) as TddReceipt;
    return result("agentic-tdd-receipt", validTddReceipt(receipt, packet, paths), refs.join(","));
  } catch (error) {
    return result("agentic-tdd-receipt", false, error instanceof Error ? error.message : String(error));
  }
}
