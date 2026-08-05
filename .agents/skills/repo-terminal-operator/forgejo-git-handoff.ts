import { realpathSync } from "node:fs";
import { runBoundedProcess } from "./bounded-subprocess";

export type ForgejoGitHandoffRequest = {
  schema_version: "forgejo-git-handoff-request@v1";
  request_id: string;
  operation: "configure_remote_and_push";
  target: {
    base_url: "http://127.0.0.1:3000";
    repository_full_name: string;
  };
  git: {
    remote_name: "forgejo";
    expected_head: string;
    source_ref: "HEAD";
    target_ref: "refs/heads/main";
    preserve_origin: true;
    force_push: false;
    set_upstream: false;
  };
};

export type ForgejoPrBranchHandoffRequest = {
  schema_version: "forgejo-pr-branch-handoff-request@v1";
  request_id: string;
  operation: "create_pr_branch";
  target: {
    base_url: "http://127.0.0.1:3000";
    repository_full_name: string;
  };
  git: {
    remote_name: "forgejo";
    expected_head: string;
    source_ref: "HEAD";
    target_ref: string;
    create_only: true;
    preserve_origin: true;
    force_push: false;
    set_upstream: false;
  };
};

export type ForgejoHandoffRequest =
  | ForgejoGitHandoffRequest
  | ForgejoPrBranchHandoffRequest;

type Stage = {
  id: string;
  exit_code: number;
  timed_out: boolean;
  cancelled: boolean;
  cleanup_complete: boolean;
};

export type ForgejoGitHandoffReceipt = {
  schema_version: "forgejo-git-handoff-receipt@v1";
  request_id: string;
  status: "passed";
  expected_head: string;
  remote_head: string;
  remote_name: "forgejo";
  remote_action: "added" | "reused";
  origin_unchanged: true;
  force_push: false;
  set_upstream: false;
  stages: Stage[];
};

export type ForgejoPrBranchHandoffReceipt = {
  schema_version: "forgejo-pr-branch-handoff-receipt@v1";
  request_id: string;
  status: "passed";
  expected_head: string;
  remote_head: string;
  target_ref: string;
  remote_name: "forgejo";
  remote_action: "added" | "reused";
  create_only: true;
  origin_unchanged: true;
  force_push: false;
  set_upstream: false;
  stages: Stage[];
};

export class GitHandoffFailure extends Error {
  constructor(
    readonly reason: string,
    detail: string,
    options?: ErrorOptions,
  ) {
    super(`${reason}: ${detail}`, options);
    this.name = "GitHandoffFailure";
  }
}

type Options = { workspaceRoot: string; remoteUrl?: string };

function safeDetail(value: string): string {
  return value
    .replace(/(https?:\/\/)[^/@\s]+@/g, "$1<redacted>@")
    .trim()
    .slice(0, 1000);
}

export function forgejoPushArgs(request: ForgejoHandoffRequest): string[] {
  const args = ["push"];
  if (request.schema_version === "forgejo-pr-branch-handoff-request@v1")
    args.push(`--force-with-lease=${request.git.target_ref}:`);
  args.push(
    request.git.remote_name,
    `${request.git.expected_head}:${request.git.target_ref}`,
  );
  return args;
}

export async function executeForgejoGitHandoff(
  request: ForgejoHandoffRequest,
  options: Options,
): Promise<ForgejoGitHandoffReceipt | ForgejoPrBranchHandoffReceipt> {
  const workspaceRoot = realpathSync(options.workspaceRoot);
  const git = Bun.which("git");
  if (!git) throw new GitHandoffFailure("git-missing", "git is unavailable");
  const stages: Stage[] = [];
  const runGit = async (id: string, args: string[]) => {
    const result = await runBoundedProcess([git, ...args], {
      cwd: workspaceRoot,
      timeoutMs: id === "push" ? 60_000 : 10_000,
    });
    const cleanupComplete =
      result.processReaped &&
      result.timerCleared &&
      result.stdoutConsumed &&
      result.stderrConsumed &&
      result.cleanupErrors.length === 0;
    stages.push({
      id,
      exit_code: result.exitCode,
      timed_out: result.timedOut,
      cancelled: result.cancelled,
      cleanup_complete: cleanupComplete,
    });
    if (result.exitCode !== 0 || !cleanupComplete)
      throw new GitHandoffFailure(
        `${id}-failed`,
        safeDetail(
          result.stderr || result.stdout || "subprocess cleanup failed",
        ),
      );
    return result.stdout.trim();
  };

  const initialHead = await runGit("head-before", ["rev-parse", "HEAD"]);
  if (initialHead !== request.git.expected_head)
    throw new GitHandoffFailure(
      "expected-head-mismatch",
      `${request.git.expected_head} != ${initialHead}`,
    );
  const remoteNames = (await runGit("remote-list-before", ["remote"]))
    .split("\n")
    .filter(Boolean);
  const originBefore = remoteNames.includes("origin")
    ? await runGit("origin-before", ["remote", "get-url", "origin"])
    : null;
  const remoteUrl =
    options.remoteUrl ??
    `http://localhost:3000/${request.target.repository_full_name}.git`;
  let remoteAction: "added" | "reused" = "added";
  if (remoteNames.includes(request.git.remote_name)) {
    const current = await runGit("forgejo-before", [
      "remote",
      "get-url",
      request.git.remote_name,
    ]);
    if (current !== remoteUrl)
      throw new GitHandoffFailure(
        "remote-url-conflict",
        `${safeDetail(current)} != ${safeDetail(remoteUrl)}`,
      );
    remoteAction = "reused";
  } else {
    await runGit("remote-add", [
      "remote",
      "add",
      request.git.remote_name,
      remoteUrl,
    ]);
  }
  const executeAfterRemote = async (): Promise<
    ForgejoGitHandoffReceipt | ForgejoPrBranchHandoffReceipt
  > => {
    const headBeforePush = await runGit("head-before-push", [
      "rev-parse",
      "HEAD",
    ]);
    if (headBeforePush !== request.git.expected_head)
      throw new GitHandoffFailure(
        "head-drift-before-push",
        `${request.git.expected_head} != ${headBeforePush}`,
      );
    const prBranch =
      request.schema_version === "forgejo-pr-branch-handoff-request@v1";
    if (prBranch) {
      await runGit("target-ref-check", [
        "check-ref-format",
        request.git.target_ref,
      ]);
      const targetBefore = await runGit("target-read-before", [
        "ls-remote",
        request.git.remote_name,
        request.git.target_ref,
      ]);
      if (targetBefore)
        throw new GitHandoffFailure(
          "target-ref-exists",
          `${request.git.target_ref} already exists`,
        );
    }
    await runGit("push", forgejoPushArgs(request));
    const remoteLine = await runGit("remote-readback", [
      "ls-remote",
      request.git.remote_name,
      request.git.target_ref,
    ]);
    const remoteHead = remoteLine.split(/\s+/)[0] ?? "";
    if (remoteHead !== request.git.expected_head)
      throw new GitHandoffFailure(
        "remote-head-mismatch",
        `${request.git.expected_head} != ${remoteHead}`,
      );
    const finalHead = await runGit("head-after", ["rev-parse", "HEAD"]);
    if (finalHead !== request.git.expected_head)
      throw new GitHandoffFailure(
        "head-drift-after-push",
        `${request.git.expected_head} != ${finalHead}`,
      );
    const finalRemoteNames = (await runGit("remote-list-after", ["remote"]))
      .split("\n")
      .filter(Boolean);
    const originAfter = finalRemoteNames.includes("origin")
      ? await runGit("origin-after", ["remote", "get-url", "origin"])
      : null;
    if (originAfter !== originBefore)
      throw new GitHandoffFailure(
        "origin-mutated",
        `${safeDetail(originBefore ?? "absent")} != ${safeDetail(originAfter ?? "absent")}`,
      );
    if (prBranch) {
      return {
        schema_version: "forgejo-pr-branch-handoff-receipt@v1",
        request_id: request.request_id,
        status: "passed",
        expected_head: request.git.expected_head,
        remote_head: remoteHead,
        target_ref: request.git.target_ref,
        remote_name: "forgejo",
        remote_action: remoteAction,
        create_only: true,
        origin_unchanged: true,
        force_push: false,
        set_upstream: false,
        stages,
      };
    }
    return {
      schema_version: "forgejo-git-handoff-receipt@v1",
      request_id: request.request_id,
      status: "passed",
      expected_head: request.git.expected_head,
      remote_head: remoteHead,
      remote_name: "forgejo",
      remote_action: remoteAction,
      origin_unchanged: true,
      force_push: false,
      set_upstream: false,
      stages,
    };
  };
  try {
    return await executeAfterRemote();
  } catch (error) {
    if (remoteAction === "added") {
      try {
        await runGit("remote-remove-rollback", [
          "remote",
          "remove",
          request.git.remote_name,
        ]);
      } catch (rollbackError) {
        throw new GitHandoffFailure(
          "remote-rollback-failed",
          safeDetail(
            rollbackError instanceof Error
              ? rollbackError.message
              : String(rollbackError),
          ),
          { cause: error },
        );
      }
    }
    throw error;
  }
}
