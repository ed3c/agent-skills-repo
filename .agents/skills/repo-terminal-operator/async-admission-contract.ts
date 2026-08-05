export type AsyncFacadeStartRequest = {
  schema_version: "repo-async-production-facade-request@v1";
  action: "start";
  run_id: string;
  job_id: string;
  target_repo: string;
  expected_head: string;
  deadline_at: string;
  candidate_refs: string[];
  foreground_receipt_ref: string;
  production_job_ref: string;
};

export type AsyncFacadeDependencies = {
  head(sourceRoot: string): string;
};

export type AsyncFacadeInspectRequest = {
  schema_version: "repo-async-production-facade-request@v1";
  action: "inspect";
  run_id: string;
  observed_at: string;
};

export type AsyncFacadeCancelRequest = {
  schema_version: "repo-async-production-facade-request@v1";
  action: "cancel";
  run_id: string;
  expected_version: number;
  reason: string;
  observed_at: string;
};

export type AsyncFacadeAdmitRequest = {
  schema_version: "repo-async-production-facade-request@v1";
  action: "admit";
  run_id: string;
  expected_version: number;
  expected_seal_sha256: string;
  observed_at: string;
};

export type AsyncFacadeRequest =
  | AsyncFacadeStartRequest
  | AsyncFacadeInspectRequest
  | AsyncFacadeCancelRequest
  | AsyncFacadeAdmitRequest;

export type AsyncFacadeStartCompletion = {
  schema_version: "repo-async-production-facade-completion@v1";
  action: "start";
  status: "awaiting-production";
  run_id: string;
  version: 0;
  seal_sha256: string;
  candidate_sha256: string;
  publication: "published" | "matched-existing";
  admission_eligible: false;
};

export type AsyncFacadeInspectCompletion = {
  schema_version: "repo-async-production-facade-completion@v1";
  action: "inspect";
  status:
    | "awaiting-production"
    | "running-production"
    | "verified"
    | "failed"
    | "stale"
    | "cancelled";
  run_id: string;
  version: number;
  seal_sha256: string;
  candidate_sha256: string;
  next_action: string;
  admission_eligible: false;
};

export type AsyncFacadeCancelCompletion = {
  schema_version: "repo-async-production-facade-completion@v1";
  action: "cancel";
  status: "cancelled";
  run_id: string;
  version: number;
  seal_sha256: string;
  candidate_sha256: string;
  diagnostic: string;
  next_action: string;
  admission_eligible: false;
};

export type AsyncFacadeAdmitCompletion = {
  schema_version: "repo-async-production-facade-completion@v1";
  action: "admit";
  status: "admitted" | "already-admitted";
  run_id: string;
  version: number;
  admission_ref: string;
  admission_sha256: string;
  publication: "published" | "matched-existing";
  admission_eligible: boolean;
};

export type AsyncFacadeCompletion =
  | AsyncFacadeStartCompletion
  | AsyncFacadeInspectCompletion
  | AsyncFacadeCancelCompletion
  | AsyncFacadeAdmitCompletion;

export function parseJsonRecord(
  bytes: Uint8Array,
  label: string,
): Record<string, unknown> {
  const value = JSON.parse(Buffer.from(bytes).toString("utf8")) as unknown;
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label}-invalid`);
  }
  return value as Record<string, unknown>;
}

export function exactArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function assertOnlyKeys(
  value: Record<string, unknown> | undefined,
  allowed: readonly string[],
  label: string,
): void {
  if (!value || Object.keys(value).some((key) => !allowed.includes(key))) {
    throw new Error(`${label}-fields-invalid`);
  }
}

function requiredString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(`${label}-invalid`);
  }
  return value;
}

function requiredInteger(value: unknown, label: string): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw new Error(`${label}-invalid`);
  }
  return Number(value);
}

export function parseAsyncFacadeRequest(bytes: Uint8Array): AsyncFacadeRequest {
  const input = parseJsonRecord(bytes, "facade-request");
  if (input.schema_version !== "repo-async-production-facade-request@v1") {
    throw new Error("facade-request-schema-invalid");
  }
  const action = requiredString(input.action, "facade-action");
  const runId = requiredString(input.run_id, "run-id");
  if (action === "start") {
    assertOnlyKeys(
      input,
      [
        "schema_version",
        "action",
        "run_id",
        "job_id",
        "target_repo",
        "expected_head",
        "deadline_at",
        "candidate_refs",
        "foreground_receipt_ref",
        "production_job_ref",
      ],
      "facade-request",
    );
    if (
      !Array.isArray(input.candidate_refs) ||
      !input.candidate_refs.every((value) => typeof value === "string")
    ) {
      throw new Error("candidate-refs-invalid");
    }
    return {
      schema_version: input.schema_version,
      action,
      run_id: runId,
      job_id: requiredString(input.job_id, "job-id"),
      target_repo: requiredString(input.target_repo, "target-repo"),
      expected_head: requiredString(input.expected_head, "expected-head"),
      deadline_at: requiredString(input.deadline_at, "deadline-at"),
      candidate_refs: [...input.candidate_refs],
      foreground_receipt_ref: requiredString(
        input.foreground_receipt_ref,
        "foreground-receipt-ref",
      ),
      production_job_ref: requiredString(
        input.production_job_ref,
        "production-job-ref",
      ),
    };
  }
  if (action === "inspect") {
    assertOnlyKeys(
      input,
      ["schema_version", "action", "run_id", "observed_at"],
      "facade-request",
    );
    return {
      schema_version: input.schema_version,
      action,
      run_id: runId,
      observed_at: requiredString(input.observed_at, "observed-at"),
    };
  }
  if (action === "cancel") {
    assertOnlyKeys(
      input,
      [
        "schema_version",
        "action",
        "run_id",
        "expected_version",
        "reason",
        "observed_at",
      ],
      "facade-request",
    );
    return {
      schema_version: input.schema_version,
      action,
      run_id: runId,
      expected_version: requiredInteger(
        input.expected_version,
        "expected-version",
      ),
      reason: requiredString(input.reason, "cancel-reason"),
      observed_at: requiredString(input.observed_at, "observed-at"),
    };
  }
  if (action === "admit") {
    assertOnlyKeys(
      input,
      [
        "schema_version",
        "action",
        "run_id",
        "expected_version",
        "expected_seal_sha256",
        "observed_at",
      ],
      "facade-request",
    );
    return {
      schema_version: input.schema_version,
      action,
      run_id: runId,
      expected_version: requiredInteger(
        input.expected_version,
        "expected-version",
      ),
      expected_seal_sha256: requiredString(
        input.expected_seal_sha256,
        "expected-seal-sha256",
      ),
      observed_at: requiredString(input.observed_at, "observed-at"),
    };
  }
  throw new Error("facade-action-invalid");
}
