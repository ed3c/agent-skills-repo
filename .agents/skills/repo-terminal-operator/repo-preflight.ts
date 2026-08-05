import { readFileSync, realpathSync, statSync } from "node:fs";
import { isAbsolute, relative, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { claimSetCheck, tddReceiptCheck } from "./repo-preflight-evidence";

export type PreflightCheck = { id: string; status: "passed" | "failed"; detail: string };

type TerminalPacket = {
  schema_version: "terminal-slice-packet@v1" | "terminal-slice-packet@v2";
  terminal_slice_id?: string;
  guided_claim_ids: string[];
  claim_set?: { ref: string; sha256: string };
  agentic_execution?: {
    tdd: { red_receipt: string; green_receipt: string; tests_immutable_during_green: boolean };
    minimal_diff: { allowed_paths_only: boolean; unrelated_refactors: boolean };
  };
  target_repo: string;
  entrypoint?: string[];
  allowed_paths: string[];
  write_lease: { expires_at: string; expected_head: string };
};

function passed(id: string, detail: string): PreflightCheck {
  return { id, status: "passed", detail };
}

function failed(id: string, detail: string): PreflightCheck {
  return { id, status: "failed", detail };
}

function diagnostic(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function contained(base: string, candidate: string): boolean {
  const local = relative(base, candidate);
  return !isAbsolute(local) && local !== ".." && !local.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`);
}

function inputContract(inputPath: string, raw: Record<string, unknown> | null, workspaceRoot: string): PreflightCheck {
  const schema = raw?.schema_version === "terminal-slice-packet@v2" ? "terminal-slice-packet@v2" : "terminal-slice-packet@v1";
  const validation = spawnSync("bun", [
    "run", resolve(workspaceRoot, "runtime/contracts/validate-packet.ts"),
    "--schema", schema, "--input", inputPath,
  ], { cwd: workspaceRoot, encoding: "utf8" });
  const detail = validation.stdout.trim() || validation.stderr.trim();
  return validation.status === 0 ? passed("input-contract", detail) : failed("input-contract", detail);
}

function readPacket(inputPath: string): { packet: TerminalPacket | null; raw: Record<string, unknown> | null; check?: PreflightCheck } {
  try {
    const parsed: unknown = JSON.parse(readFileSync(inputPath, "utf8"));
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { packet: null, raw: null, check: failed("input-readable", "terminal packet must be an object") };
    }
    return { packet: parsed as TerminalPacket, raw: parsed as Record<string, unknown> };
  } catch (error) {
    return { packet: null, raw: null, check: failed("input-readable", diagnostic(error)) };
  }
}

function entrypointCheck(packet: TerminalPacket, target: string, workspaceRoot: string): PreflightCheck {
  const scriptArg = packet.entrypoint?.[2];
  if (!scriptArg || packet.entrypoint?.[0] !== "bun" || packet.entrypoint[1] !== "run") {
    return failed("entrypoint-reachable", "entrypoint must begin with bun run <script>");
  }
  try {
    const realScript = realpathSync(resolve(target, scriptArg));
    if (!contained(realpathSync(workspaceRoot), realScript)) return failed("entrypoint-reachable", "entrypoint escapes workspace");
    if (!statSync(realScript).isFile()) return failed("entrypoint-reachable", "entrypoint is not a regular file");
    return passed("entrypoint-reachable", relative(target, realScript));
  } catch (error) {
    return failed("entrypoint-reachable", diagnostic(error));
  }
}

function allowedPathsCheck(packet: TerminalPacket, outputRepo: string): PreflightCheck {
  const escaped = packet.allowed_paths.filter((path) => isAbsolute(path) || !contained(outputRepo, resolve(outputRepo, path)));
  return escaped.length === 0
    ? passed("allowed-paths", `${packet.allowed_paths.length} path(s)`)
    : failed("allowed-paths", escaped.join(","));
}

function packetChecks(packet: TerminalPacket, workspaceRoot: string, outputRepo: string): PreflightCheck[] {
  const target = resolve(workspaceRoot, packet.target_repo);
  const actualHead = spawnSync("git", ["-C", workspaceRoot, "rev-parse", "HEAD"], { encoding: "utf8" }).stdout.trim();
  const expiresAt = Date.parse(packet.write_lease.expires_at);
  return [
    target === outputRepo ? passed("target-repo", relative(workspaceRoot, target)) : failed("target-repo", relative(workspaceRoot, target)),
    entrypointCheck(packet, target, workspaceRoot),
    packet.write_lease.expected_head === actualHead ? passed("expected-head", actualHead) : failed("expected-head", actualHead),
    Number.isFinite(expiresAt) && expiresAt > Date.now() ? passed("live-lease", packet.write_lease.expires_at) : failed("live-lease", packet.write_lease.expires_at),
    allowedPathsCheck(packet, outputRepo), claimSetCheck(packet, workspaceRoot), tddReceiptCheck(packet, workspaceRoot),
  ];
}

export function runPreflightChecks(input: string, workspaceRoot: string, outputRepo: string): PreflightCheck[] {
  const inputPath = resolve(input);
  const loaded = readPacket(inputPath);
  const checks = [inputContract(inputPath, loaded.raw, workspaceRoot)];
  if (loaded.check) checks.push(loaded.check);
  if (loaded.packet) checks.push(...packetChecks(loaded.packet, workspaceRoot, outputRepo));
  return checks;
}
