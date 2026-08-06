#!/usr/bin/env python3
"""Audit that an external development Ed25519 key is absent from Git/worktree."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.sandbox_executor.key_audit import (  # noqa: E402
    KeyAuditError,
    audit_development_private_key,
    write_key_audit,
)
from skill_arena.sandbox_executor.model import load_json_object  # noqa: E402

DEFAULT_SCHEMA = ROOT / "contracts/development-private-key-audit.schema.json"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--repo-root", type=Path, default=ROOT)
    value.add_argument("--private-key", type=Path, required=True)
    value.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    value.add_argument("--output", type=Path, required=True)
    value.add_argument("--refs-output", type=Path, required=True)
    return value


def _schema_errors(audit: object, schema_path: Path) -> list[str]:
    schema = load_json_object(schema_path)
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema).iter_errors(audit),
            key=lambda error: (list(error.absolute_path), error.message),
        )
    ]


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        audit, refs = audit_development_private_key(
            args.repo_root,
            args.private_key,
        )
        errors = _schema_errors(audit, args.schema)
        if errors:
            raise KeyAuditError(
                "development key audit violates its schema: " + "; ".join(errors)
            )
        write_key_audit(args.output, args.refs_output, audit, refs)
    except (KeyAuditError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: development private-key audit: {exc}", file=sys.stderr)
        return 2
    print(
        "PASS: development private key absent from all audited Git objects and "
        f"worktree files; audit_digest={audit['audit_digest']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
