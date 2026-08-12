#!/usr/bin/env python3
"""Validate or physically preflight one pinned local Arena provider policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.experiment.model import ExperimentError  # noqa: E402
from skill_arena.experiment.model import sha256_json  # noqa: E402
from skill_arena.experiment.provider_policy import (  # noqa: E402
    LocalProviderPolicy,
    OllamaHttpProbe,
    load_provider_policy,
    load_provider_revocations,
    _preflight_provider,
)


def _policy_summary(policy: LocalProviderPolicy) -> dict[str, object]:
    return {
        "status": "policy-valid",
        "policy_id": policy.policy_id,
        "policy_digest": policy.digest,
        "provider_ready": False,
        "experiment_execution_authorized": policy.experiment_execution_authorized,
    }


def _write_exclusive(path: Path, value: object) -> None:
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir():
        raise ExperimentError("provider preflight output parent is absent or unsafe")
    body = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(body)
    except FileExistsError as exc:
        raise ExperimentError("provider preflight output already exists") from exc
    except OSError as exc:
        raise ExperimentError(f"cannot write provider preflight output: {path}") from exc


def _require_available_target(path: Path, label: str) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ExperimentError(f"{label} parent is absent or unsafe")
    if path.exists() or path.is_symlink():
        raise ExperimentError(f"{label} already exists")


def _attempt_value(
    policy: LocalProviderPolicy,
    revocations: dict[str, object],
    *,
    attempt_id: str,
    started_at: str,
    status: str,
    model_invocation_count: int,
    completed_at: str | None = None,
    diagnostic: str | None = None,
    receipt_digest: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "arena-provider-attempt@1",
        "attempt_id": attempt_id,
        "policy_id": policy.policy_id,
        "policy_digest": policy.digest,
        "coordination_issue_url": policy.coordination_issue_url,
        "observer_kind": "local-owner-session",
        "observer_actor_id": policy.observation_actor_id,
        "observation_authority_id": policy.observation_authority_id,
        "observer_host_digest": "sha256:"
        + hashlib.sha256(platform.node().encode("utf-8")).hexdigest(),
        "runtime_identity": f"{platform.system().lower()}-{platform.machine().lower()}-python-{platform.python_version()}",
        "revocation_registry_digest": revocations["registry_digest"],
        "started_at": started_at,
        "completed_at": completed_at,
        "status": status,
        "model_invocation_count": model_invocation_count,
        "diagnostic": diagnostic,
        "receipt_digest": receipt_digest,
    }
    return {**value, "attempt_digest": sha256_json(value)}


def _replace_attempt(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    body = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ExperimentError("cannot finalize provider attempt receipt") from exc


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--policy", type=Path, required=True)
    result.add_argument("--validate-only", action="store_true")
    result.add_argument("--output", type=Path)
    result.add_argument("--attempt-receipt", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        policy = load_provider_policy(args.policy)
        revocations = load_provider_revocations(
            ROOT / policy.revocation_registry_path
        )
        if args.validate_only:
            if args.output is not None or args.attempt_receipt is not None:
                raise ExperimentError("--validate-only cannot write a preflight receipt")
            print(json.dumps(_policy_summary(policy), sort_keys=True))
            return 0
        if args.output is None:
            raise ExperimentError("physical provider preflight requires --output")
        if args.attempt_receipt is None:
            raise ExperimentError(
                "physical provider preflight requires --attempt-receipt"
            )
        if args.output == args.attempt_receipt:
            raise ExperimentError("provider output and attempt receipt must differ")
        _require_available_target(args.output, "provider preflight output")
        _require_available_target(args.attempt_receipt, "provider attempt receipt")
        started = datetime.now(timezone.utc)
        started_at = started.isoformat().replace("+00:00", "Z")
        attempt_id = f"provider-preflight-{uuid.uuid4()}"
        model_invocation_count = 0
        _write_exclusive(
            args.attempt_receipt,
            _attempt_value(
                policy,
                revocations,
                attempt_id=attempt_id,
                started_at=started_at,
                status="started",
                model_invocation_count=0,
            ),
        )

        def before_model_invocation() -> None:
            nonlocal model_invocation_count
            model_invocation_count = 1
            _replace_attempt(
                args.attempt_receipt,
                _attempt_value(
                    policy,
                    revocations,
                    attempt_id=attempt_id,
                    started_at=started_at,
                    status="invoking",
                    model_invocation_count=1,
                ),
            )

        try:
            receipt = _preflight_provider(
                policy,
                probe=OllamaHttpProbe(),
                checked_at=datetime.now(timezone.utc),
                attempt_id=attempt_id,
                before_model_invocation=before_model_invocation,
            )
            _write_exclusive(args.output, receipt)
        except ExperimentError as exc:
            completed_at = datetime.now(timezone.utc).isoformat().replace(
                "+00:00", "Z"
            )
            _replace_attempt(
                args.attempt_receipt,
                _attempt_value(
                    policy,
                    revocations,
                    attempt_id=attempt_id,
                    started_at=started_at,
                    completed_at=completed_at,
                    status="failed",
                    model_invocation_count=model_invocation_count,
                    diagnostic=str(exc),
                ),
            )
            raise
        completed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _replace_attempt(
            args.attempt_receipt,
            _attempt_value(
                policy,
                revocations,
                attempt_id=attempt_id,
                started_at=started_at,
                completed_at=completed_at,
                status="succeeded",
                model_invocation_count=model_invocation_count,
                receipt_digest=str(receipt["receipt_digest"]),
            ),
        )
        print(
            json.dumps(
                {
                    "status": "provider-ready",
                    "policy_id": policy.policy_id,
                    "receipt_digest": receipt["receipt_digest"],
                    "output": str(args.output),
                    "experiment_execution_authorized": policy.experiment_execution_authorized,
                },
                sort_keys=True,
            )
        )
        return 0
    except ExperimentError as exc:
        print(f"FAIL: Arena provider preflight: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
