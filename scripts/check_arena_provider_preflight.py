#!/usr/bin/env python3
"""Verify one Arena provider capability receipt without provider access."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.experiment.model import ExperimentError  # noqa: E402
from skill_arena.experiment.provider_policy import (  # noqa: E402
    load_provider_policy,
    load_provider_revocations,
    validate_provider_attempt,
    validate_provider_preflight,
)


def _load_receipt(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ExperimentError("provider preflight receipt is absent or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read provider preflight receipt: {path}") from exc
    if not isinstance(value, dict):
        raise ExperimentError("provider preflight receipt root must be an object")
    return value


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--policy", type=Path, required=True)
    result.add_argument("--receipt", type=Path)
    result.add_argument("--attempt-receipt", type=Path, required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        policy = load_provider_policy(args.policy)
        revocations = load_provider_revocations(
            ROOT / policy.revocation_registry_path
        )
        receipt = (
            validate_provider_preflight(_load_receipt(args.receipt), policy)
            if args.receipt is not None
            else None
        )
        attempt = validate_provider_attempt(
            _load_receipt(args.attempt_receipt), policy, receipt, revocations
        )
        if receipt is None:
            print(
                json.dumps(
                    {
                        "status": "verified-failed-attempt",
                        "policy_digest": policy.digest,
                        "attempt_digest": attempt["attempt_digest"],
                        "model_invocation_count": attempt["model_invocation_count"],
                        "experiment_execution_authorized": policy.experiment_execution_authorized,
                    },
                    sort_keys=True,
                )
            )
            return 0
        print(
            json.dumps(
                {
                    "status": "verified",
                    "policy_digest": policy.digest,
                    "receipt_digest": receipt["receipt_digest"],
                    "attempt_digest": attempt["attempt_digest"],
                    "receipt_verified": True,
                    "observed_ready": receipt["ready"],
                    "checked_at": receipt["checked_at"],
                    "experiment_execution_authorized": policy.experiment_execution_authorized,
                },
                sort_keys=True,
            )
        )
        return 0
    except ExperimentError as exc:
        print(f"FAIL: Arena provider preflight receipt: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
