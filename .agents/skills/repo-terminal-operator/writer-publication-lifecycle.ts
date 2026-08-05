export class WriterPublicationFailure extends Error {
  constructor(readonly artifactCreated: boolean, cause: unknown) {
    super("writer publication failed", { cause });
  }
}

export function publicationResult<T>(
  result: T | undefined,
  primary: unknown,
  cleanupFailures: unknown[],
  artifactCreated: boolean,
): T {
  const failures = primary === undefined ? cleanupFailures : [primary, ...cleanupFailures];
  if (failures.length > 0) {
    const cause = failures.length === 1 ? failures[0] : new AggregateError(failures, "writer publication and cleanup failed");
    throw new WriterPublicationFailure(artifactCreated, cause);
  }
  if (result === undefined) throw new WriterPublicationFailure(artifactCreated, new Error("publication returned no result"));
  return result;
}
