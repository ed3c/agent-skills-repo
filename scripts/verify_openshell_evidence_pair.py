#!/usr/bin/env python3
"""Admit and index two OpenShell physical evidence bundles offline."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.core import EvidenceRejected  # noqa: E402
from skill_arena.sandbox_executor import (  # noqa: E402
    EvidencePairError,
    SandboxCase,
    SandboxExecutorError,
    SandboxProfile,
    load_json_object,
    load_public_key,
    verify_evidence_pair,
    write_pair_index,
)

DEFAULT_CASE = ROOT / "data/sandbox_cases/smoke-python.json"
DEFAULT_PROFILE = ROOT / "data/sandbox_profiles/openshell-0.0.59-docker.json"
DEFAULT_SCHEMA = ROOT / "contracts/openshell-physical-evidence-pair.schema.json"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--bundle", type=Path, action="append", required=True)
    value.add_argument("--case", type=Path, default=DEFAULT_CASE)
    value.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    value.add_argument("--public-key", type=Path, required=True)
    value.add_argument("--private-key", type=Path, required=True)
    value.add_argument("--issuer-key-id", required=True)
    value.add_argument("--benchmark-suite-digest", required=True)
    value.add_argument("--skill-artifact-digest", required=True)
    value.add_argument("--repo-root", type=Path, default=ROOT)
    value.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    value.add_argument("--generated-at")
    value.add_argument("--output", type=Path, required=True)
    return value


def _timestamp(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise EvidencePairError("--generated-at must be an ISO-8601 timestamp") from exc
    if value.tzinfo is None:
        raise EvidencePairError("--generated-at must include a timezone")
    return value


def _schema_errors(index: object, schema_path: Path) -> list[str]:
    schema = load_json_object(schema_path)
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(
            Draft202012Validator(
                schema,
                format_checker=FormatChecker(),
            ).iter_errors(index),
            key=lambda error: (list(error.absolute_path), error.message),
        )
    ]


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if len(args.bundle) != 2:
            raise EvidencePairError("--bundle must be provided exactly twice")
        case = SandboxCase.from_mapping(load_json_object(args.case))
        profile = SandboxProfile.from_mapping(load_json_object(args.profile))
        public_key = load_public_key(args.public_key)
        index = verify_evidence_pair(
            args.bundle,
            case=case,
            profile=profile,
            public_key=public_key,
            private_key_path=args.private_key,
            issuer_key_id=args.issuer_key_id,
            benchmark_suite_digest=args.benchmark_suite_digest,
            skill_artifact_digest=args.skill_artifact_digest,
            repo_root=args.repo_root,
            generated_at=_timestamp(args.generated_at),
        )
        errors = _schema_errors(index, args.schema)
        if errors:
            raise EvidencePairError(
                "physical evidence index violates its schema: " + "; ".join(errors)
            )
        write_pair_index(args.output, index)
    except (
        EvidencePairError,
        EvidenceRejected,
        SandboxExecutorError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"FAIL: OpenShell physical evidence pair: {exc}", file=sys.stderr)
        return 2
    print(
        "PASS: admitted two OpenShell physical evidence bundles; "
        f"pair_digest={index['pair_digest']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
