"""Distinct fail-closed sandbox executor outcomes."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    CONFIG_INVALID = 64
    SIGNING_KEY_INVALID = 65
    OUTPUT_EXISTS = 66
    GATEWAY_UNAVAILABLE = 70
    SUBSTRATE_UNAVAILABLE = 71
    SANDBOX_CREATE_FAILED = 72
    TRANSPORT_FAILED = 73
    EVIDENCE_INCOMPLETE = 74
    SANDBOX_DELETE_FAILED = 75
    WALL_WINDOW_EXCEEDED = 76
    TASK_RESULT_MISMATCH = 77


class SandboxExecutorError(RuntimeError):
    """A distinct executor outcome that must never be collapsed into success."""

    def __init__(self, code: ExitCode, message: str) -> None:
        super().__init__(message)
        self.code = code
