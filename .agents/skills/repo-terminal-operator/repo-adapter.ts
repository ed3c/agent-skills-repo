#!/usr/bin/env bun
import { readFileSync } from "node:fs";
import { join, relative, resolve } from "node:path";
import { runPreflightChecks } from "./repo-preflight";
import { runSmallLoop } from "./small-loop-runner";

const root = import.meta.dir;
const outputRepo = resolve(root, "../../..");
const workspaceRoot = resolve(outputRepo, "../..");

function profile(name: string): { schema_version: string; commands: string[][] } {
  return JSON.parse(readFileSync(join(root, name), "utf8")) as { schema_version: string; commands: string[][] };
}

function describe() {
  const codeQuality = profile("code-quality.profile.json");
  const productionUse = profile("production-use.profile.json");
  return {
    schema_version: "repo-terminal-operator-description@v1",
    operator: "repo-terminal-operator",
    input_contract: "terminal-slice-packet@v2",
    compatible_input_contracts: ["terminal-slice-packet@v1", "terminal-slice-packet@v2"],
    output_contract: "small-loop-run-receipt@v1",
    code_quality_commands: codeQuality.commands.length,
    production_use_commands: productionUse.commands.length,
  };
}

function preflight(inputPath: string): number {
  const checks = runPreflightChecks(inputPath, workspaceRoot, outputRepo);

  const status = checks.every((check) => check.status === "passed") ? "passed" : "failed";
  console.log(JSON.stringify({
    schema_version: "repo-terminal-preflight-receipt@v1",
    status,
    input_path: resolve(inputPath),
    target_repo: relative(workspaceRoot, outputRepo),
    checks,
  }));
  return status === "passed" ? 0 : 2;
}

async function run(inputPath: string): Promise<number> {
  const receipt = await runSmallLoop(inputPath);
  console.log(JSON.stringify(receipt));
  return receipt.status === "passed" ? 0 : 2;
}

function describeOrSelftest(action: string): number {
  if (action !== "--describe" && action !== "--selftest") return 64;
  const result = describe();
  if (action === "--selftest" && (result.code_quality_commands < 1 || result.production_use_commands < 1)) {
    console.error("FAIL: both profiles require a real argv command");
    return 2;
  }
  console.log(JSON.stringify({ ...result, status: "passed" }));
  return 0;
}

async function main(args: string[]): Promise<number> {
  if (args.length === 2 && args[0] === "--preflight") return preflight(args[1]);
  if (args.length === 2 && args[0] === "--run") return run(args[1]);
  if (args.length === 1) {
    const result = describeOrSelftest(args[0]);
    if (result !== 64) return result;
  }
  console.error("usage: repo-adapter.ts --describe|--selftest|--preflight <packet>|--run <packet>");
  return 64;
}

process.exitCode = await main(process.argv.slice(2));
