#!/usr/bin/env python3
"""Self-contained in-sandbox runner for one deterministic case.

The runner inherits no provider credentials, runs one absolute executable from
the preregistered allowlist, applies OS resource limits to the child, and writes
one result object. It exits zero after recording a task failure; control-plane
failures prevent a result file and are handled by the host executor.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import resource
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, cast

INPUT_SCHEMA = "sandbox-runner-input@1"
RESULT_SCHEMA = "sandbox-task-result@1"
MAX_OUTPUT_BYTES = 1_048_576


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def command_digest(command: list[str]) -> str:
    return digest_bytes(canonical_bytes({"command": command}))


def load_input(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("runner input must be an object")
    required = {
        "schema_version",
        "workspace_nonce",
        "command",
        "allowed_tools",
        "resource_limits",
    }
    if set(value) != required or value.get("schema_version") != INPUT_SCHEMA:
        raise ValueError("runner input schema or fields are invalid")
    command = value["command"]
    allowed = value["allowed_tools"]
    limits = value["resource_limits"]
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) or not item for item in command)
        or not cast(str, command[0]).startswith("/")
    ):
        raise ValueError("command must use an absolute executable")
    if (
        not isinstance(allowed, list)
        or command[0] not in allowed
        or any(
            not isinstance(item, str) or not item.startswith("/")
            for item in allowed
        )
    ):
        raise ValueError("command executable is not preregistered")
    if not isinstance(limits, Mapping):
        raise ValueError("resource_limits must be an object")
    return cast(dict[str, object], value)


def child_limits(limits: Mapping[str, object]):
    cpu_seconds = max(
        1,
        math.ceil(cast(int, limits["cpu_time_ms"]) / 1000),
    )
    memory_bytes = cast(int, limits["memory_bytes"])
    process_count = cast(int, limits["process_count_max"])

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(
                resource.RLIMIT_NPROC,
                (process_count, process_count),
            )

    return apply


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "usage: sandbox_case_runner.py INPUT_JSON OUTPUT_JSON",
            file=sys.stderr,
        )
        return 64
    source = Path(sys.argv[1])
    output = Path(sys.argv[2])
    try:
        value = load_input(source)
        command = cast(list[str], value["command"])
        limits = cast(Mapping[str, object], value["resource_limits"])
        nonce = cast(str, value["workspace_nonce"])
        if not nonce:
            raise ValueError("workspace_nonce is empty")
        sandbox_root = Path(
            os.environ.get("ARENA_SANDBOX_ROOT", "/sandbox")
        )
        workdir = sandbox_root / "work" / nonce
        workdir.mkdir(parents=True, exist_ok=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        started = datetime.now(timezone.utc)
        timed_out = False
        try:
            completed = subprocess.run(
                command,
                cwd=workdir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={
                    "HOME": str(workdir),
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/bin:/bin",
                    "TMPDIR": "/tmp",
                },
                timeout=cast(int, limits["wall_time_ms"]) / 1000,
                check=False,
                preexec_fn=child_limits(limits),
            )
            exit_code = completed.returncode
            stdout = completed.stdout
            stderr = completed.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = 124
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
        completed_at = datetime.now(timezone.utc)
        if len(stdout) > MAX_OUTPUT_BYTES or len(stderr) > MAX_OUTPUT_BYTES:
            raise ValueError("task output exceeds 1 MiB evidence limit")
        result = {
            "schema_version": RESULT_SCHEMA,
            "workspace_nonce": nonce,
            "command_digest": command_digest(command),
            "exit_code": exit_code,
            "stdout_base64": base64.b64encode(stdout).decode("ascii"),
            "stderr_base64": base64.b64encode(stderr).decode("ascii"),
            "stdout_sha256": digest_bytes(stdout),
            "stderr_sha256": digest_bytes(stderr),
            "timed_out": timed_out,
            "started_at": started.isoformat(),
            "completed_at": completed_at.isoformat(),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=".result.",
            dir=output.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    result,
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"runner failure: {exc}", file=sys.stderr)
        return 70


if __name__ == "__main__":
    raise SystemExit(main())
