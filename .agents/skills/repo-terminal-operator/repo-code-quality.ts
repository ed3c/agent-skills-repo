#!/usr/bin/env bun
import { resolve } from "node:path";
import { executeTaskQuality } from "./task-quality-runner";

const workspace = resolve(import.meta.dir, "../../../../..");
const adapter = resolve(
  workspace,
  "loop_wiki/evolve-unknown-discovery-plan-truth/adapters/typescript",
);
const receipt = await executeTaskQuality({ adapter, workspace });
console.log(JSON.stringify(receipt));
process.exitCode = receipt.status === "passed" ? 0 : 2;
