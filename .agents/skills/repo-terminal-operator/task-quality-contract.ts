import { createHash } from "node:crypto";

export type TaskQualityCwd = "adapter" | "workspace";
export type TaskQualityStageDefinition = {
  id: string;
  phase: number;
  cwd: TaskQualityCwd;
  command: string[];
  timeout_ms: number;
};

const operator =
  "../../../../repo/agent-skills-repo/.agents/skills/repo-terminal-operator";
const typedSources = [
  `${operator}/repo-code-quality.ts`,
  `${operator}/task-quality-contract.ts`,
  `${operator}/task-quality-runner.ts`,
  `${operator}/small-loop-receipt.ts`,
  `${operator}/async-job-lifecycle.ts`,
  `${operator}/async-job-lifecycle-cli.ts`,
  `${operator}/async-admission-facade.ts`,
  `${operator}/async-admission-facade-cli.ts`,
  `${operator}/async-admission-contract.ts`,
  `${operator}/async-admission-verifier.ts`,
  `${operator}/async-worker-carrier.ts`,
  `${operator}/async-worker-carrier-cli.ts`,
  `${operator}/async-progress-store.ts`,
  `${operator}/async-control-plane.ts`,
  `${operator}/async-control-plane-cli.ts`,
  `${operator}/anchored-artifact-read.ts`,
  `${operator}/evidence-cost-cache.ts`,
  `${operator}/evidence-cost-cache-cli.ts`,
  `${operator}/evidence-cost-collector.ts`,
  `${operator}/evidence-cost-collector-cli.ts`,
  `${operator}/forgejo-git-handoff.ts`,
  `${operator}/forgejo-git-handoff-cli.ts`,
  `${operator}/writer-entrypoint.ts`,
  `${operator}/writer-publication.ts`,
  `${operator}/writer-native.ts`,
  `${operator}/writer-native-library.ts`,
  "../../../../skills/repo-neural-perception/scripts/owned-profile-command.ts",
  "../../../../skills/repo-neural-perception/scripts/measured-owned-profile-command.ts",
  "../../../../skills/repo-neural-perception/scripts/owned-stream-drain.ts",
  "../../../../runtime/contracts/validate-async-production.ts",
  "../../../../runtime/contracts/validate-packet.ts",
  "../../../../runtime/contracts/validate-evidence-cost.ts",
  "../../../../runtime/forgejo/project-git-handoff-request.ts",
  "../../../../runtime/forgejo/project-pr-branch-handoff-request.ts",
];
const lintSources = [
  ...typedSources,
  `${operator}/small-loop-gate-contract.ts`,
  "../../../../tests/skills/repo-terminal-task-quality.test.ts",
  "../../../../tests/skills/repo-terminal-async-worker-carrier.test.ts",
  "../../../../tests/skills/repo-terminal-async-control-plane.test.ts",
  "../../../../tests/skills/repo-terminal-async-admission-facade.test.ts",
  "../../../../tests/skills/repo-owned-stream-drain.test.ts",
  "../../../../tests/skills/repo-evidence-cost-cache.test.ts",
  "../../../../tests/skills/repo-evidence-cost-collector-contract.test.ts",
  "../../../../tests/skills/repo-evidence-cost-collector.production.test.ts",
  "../../../../tests/skills/repo-evidence-cost-measured-owned-command.production.test.ts",
  "../../../../tests/forgejo/git-handoff-request.cli.test.ts",
  "../../../../tests/forgejo/operation-receipts.contract.test.ts",
  "../../../../tests/forgejo/repo-local-git-handoff.test.ts",
];
const formatSources = [
  ...lintSources,
  `${operator}/SKILL.md`,
  `${operator}/code-quality.profile.json`,
  `${operator}/production-use.profile.json`,
  `${operator}/evidence-cost-collector.production.profile.json`,
  "../../../../skills/repo-neural-perception/schemas/contract-manifest.json",
  "../../../../skills/repo-neural-perception/schemas/forgejo-git-handoff-receipt.v1.json",
  "../../../../skills/repo-neural-perception/schemas/forgejo-git-handoff-request.v1.json",
  "../../../../skills/repo-neural-perception/schemas/forgejo-pr-branch-handoff-receipt.v1.json",
  "../../../../skills/repo-neural-perception/schemas/forgejo-pr-branch-handoff-request.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-worker-completion.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-worker-error.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-worker-job.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-worker-progress.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-worker-progress.v2.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-worker-request.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-worker-result.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-facade-request.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-facade-completion.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-facade-error.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-admission.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-control-request.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-control-projection.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-control-completion.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-async-production-control-error.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-source-input.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-plan-binding.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-stage.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-cache-request.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-cache-entry.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-ledger.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-observation.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-observation.v2.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-content-binding.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-stage-binding.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-stage-progress.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-collector-request.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-collector-execution.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-collector-precondition-failure.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-collector-completion.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-request-stage.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-cache-completion.v1.json",
  "../../../../skills/repo-neural-perception/schemas/repo-evidence-cost-cache-error.v1.json",
  "../../../../skills/repo-neural-perception/references/async-production-seed.md",
];

const focusedTests = [
  "tests/skills/repo-neural-writer-entrypoint.test.ts",
  "tests/skills/repo-neural-writer-source-binding.test.ts",
  "tests/skills/repo-terminal-small-loop-runner.test.ts",
  "tests/skills/repo-terminal-async-job-lifecycle.test.ts",
  "tests/skills/repo-terminal-async-control-plane.test.ts",
  "tests/skills/repo-terminal-async-admission-facade.test.ts",
  "tests/skills/repo-evidence-cost-collector-contract.test.ts",
];

export const REPO_TERMINAL_CODE_QUALITY_COMMAND = [
  "bun",
  "run",
  ".agents/skills/repo-terminal-operator/repo-code-quality.ts",
];

export const TASK_QUALITY_STAGE_DEFINITIONS: readonly TaskQualityStageDefinition[] =
  [
    {
      id: "typed-eslint",
      phase: 1,
      cwd: "adapter",
      command: [
        "./node_modules/.bin/eslint",
        "--config",
        "eslint.config.mjs",
        "--no-warn-ignored",
        ...lintSources,
      ],
      timeout_ms: 30_000,
    },
    {
      id: "format-check",
      phase: 1,
      cwd: "adapter",
      command: [
        "./node_modules/.bin/prettier",
        "--check",
        ...formatSources,
        "eslint.config.mjs",
        "tsconfig.repo-terminal.json",
      ],
      timeout_ms: 30_000,
    },
    {
      id: "strict-typecheck",
      phase: 2,
      cwd: "adapter",
      command: [
        "./node_modules/.bin/tsc",
        "--noEmit",
        "-p",
        "tsconfig.repo-terminal.json",
      ],
      timeout_ms: 30_000,
    },
    {
      id: "dependency-boundaries",
      phase: 2,
      cwd: "adapter",
      command: [
        "./node_modules/.bin/depcruise",
        "--config",
        "dependency-cruiser.cjs",
        "--output-type",
        "err",
        `${operator}/repo-code-quality.ts`,
        `${operator}/async-job-lifecycle-cli.ts`,
        `${operator}/async-admission-facade-cli.ts`,
        `${operator}/async-worker-carrier-cli.ts`,
        `${operator}/async-control-plane-cli.ts`,
        `${operator}/evidence-cost-cache-cli.ts`,
        `${operator}/evidence-cost-collector-cli.ts`,
        `${operator}/writer-entrypoint.ts`,
      ],
      timeout_ms: 30_000,
    },
    {
      id: "focused-tests",
      phase: 3,
      cwd: "workspace",
      command: ["bun", "test", ...focusedTests],
      timeout_ms: 45_000,
    },
    {
      id: "evidence-cache-tests",
      phase: 3,
      cwd: "workspace",
      command: ["bun", "test", "tests/skills/repo-evidence-cost-cache.test.ts"],
      timeout_ms: 45_000,
    },
    {
      id: "ownership-tests",
      phase: 3,
      cwd: "workspace",
      command: ["bun", "test", "tests/skills/repo-owned-stream-drain.test.ts"],
      timeout_ms: 45_000,
    },
    {
      id: "forgejo-handoff-tests",
      phase: 4,
      cwd: "workspace",
      command: [
        "bun",
        "test",
        "tests/forgejo/git-handoff-request.cli.test.ts",
        "tests/forgejo/operation-receipts.contract.test.ts",
        "tests/forgejo/repo-local-git-handoff.test.ts",
      ],
      timeout_ms: 45_000,
    },
  ];

export function taskQualityProfileSha256(): string {
  return createHash("sha256")
    .update(JSON.stringify(TASK_QUALITY_STAGE_DEFINITIONS))
    .digest("hex");
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stageErrors(
  value: unknown,
  expected: TaskQualityStageDefinition,
): string[] {
  if (!record(value)) return [`stage ${expected.id} missing`];
  const errors: string[] = [];
  if (value.id !== expected.id) errors.push(`stage ${expected.id} id`);
  if (value.phase !== expected.phase) errors.push(`stage ${expected.id} phase`);
  if (value.cwd !== expected.cwd) errors.push(`stage ${expected.id} cwd`);
  if (JSON.stringify(value.command) !== JSON.stringify(expected.command))
    errors.push(`stage ${expected.id} command`);
  if (value.status !== "passed") errors.push(`stage ${expected.id} status`);
  if (value.exit_code !== 0) errors.push(`stage ${expected.id} exit_code`);
  if (typeof value.elapsed_ms !== "number" || value.elapsed_ms < 0)
    errors.push(`stage ${expected.id} elapsed_ms`);
  for (const field of [
    "process_reaped",
    "timer_cleared",
    "stdout_consumed",
    "stderr_consumed",
  ]) {
    if (value[field] !== true) errors.push(`stage ${expected.id} ${field}`);
  }
  if (
    !Array.isArray(value.cleanup_errors) ||
    value.cleanup_errors.length !== 0
  ) {
    errors.push(`stage ${expected.id} cleanup_errors`);
  }
  if (
    expected.command[0] === "bun" &&
    expected.command[1] === "test" &&
    (typeof value.test_cases !== "number" || value.test_cases < 1)
  ) {
    errors.push(`stage ${expected.id} test_cases`);
  }
  return errors;
}

export function taskQualityReceiptErrors(
  value: Record<string, unknown>,
): string[] {
  const errors: string[] = [];
  if (value.claim_boundary !== "task-scoped-code-quality")
    errors.push("inner claim_boundary");
  if (value.profile_sha256 !== taskQualityProfileSha256())
    errors.push("inner profile_sha256");
  if (
    JSON.stringify(value.command) !==
    JSON.stringify(REPO_TERMINAL_CODE_QUALITY_COMMAND)
  ) {
    errors.push("inner command");
  }
  const coverage = record(value.coverage) ? value.coverage : {};
  if (
    coverage.status !== "not-selected" ||
    coverage.next_mode !== "production-use/writer-entrypoint"
  ) {
    errors.push("inner coverage routing");
  }
  if (
    !Array.isArray(value.stages) ||
    value.stages.length !== TASK_QUALITY_STAGE_DEFINITIONS.length
  ) {
    errors.push("inner stages");
    return errors;
  }
  value.stages.forEach((stage, index) => {
    const expected = TASK_QUALITY_STAGE_DEFINITIONS[index];
    if (expected) errors.push(...stageErrors(stage, expected));
  });
  return errors;
}
