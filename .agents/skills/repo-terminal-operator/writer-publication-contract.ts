export type WriterOutcome = "published" | "matched-existing";
export type RecoveryOutcome = "none" | "pre-link" | "post-link";
export type WriterPublication = { writerOutcome: WriterOutcome; recoveryOutcome: RecoveryOutcome };
