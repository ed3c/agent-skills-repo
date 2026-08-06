#!/usr/bin/env python3
"""Import pinned SkillsBench tasks and manage parity evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from arena_adapters.skillsbench import (  # noqa: E402
    SkillsBenchAdapterError,
    bind_execution_parity,
    import_selected_tasks,
    load_policy,
    validate_bundle_directory,
    validate_bundle_index,
)
from arena_adapters.skillsbench.common import read_json_object  # noqa: E402

DEFAULT_POLICY = ROOT / "data/skillsbench/import-policy.json"
DEFAULT_BUNDLE_SCHEMA = ROOT / "contracts/skillsbench-task-bundle.schema.json"
DEFAULT_INDEX_SCHEMA = ROOT / "contracts/skillsbench-task-index.schema.json"
DEFAULT_EXECUTION_SCHEMA = ROOT / "contracts/skillsbench-execution-evidence.schema.json"
DEFAULT_PARITY_SCHEMA = ROOT / "contracts/skillsbench-parity-report.schema.json"


def _schema_errors(instance: object, schema_path: Path, label: str) -> list[str]:
    schema = read_json_object(schema_path)
    return [
        f"{label}:{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(
            Draft202012Validator(schema).iter_errors(instance),
            key=lambda item: (list(item.absolute_path), item.message),
        )
    ]


def validate_with_schemas(bundle_dir: Path) -> None:
    validate_bundle_directory(bundle_dir)
    bundle = read_json_object(bundle_dir / "bundle.json")
    parity = read_json_object(bundle_dir / "parity.json")
    errors = [
        *_schema_errors(bundle, DEFAULT_BUNDLE_SCHEMA, str(bundle_dir / "bundle.json")),
        *_schema_errors(parity, DEFAULT_PARITY_SCHEMA, str(bundle_dir / "parity.json")),
    ]
    if errors:
        raise SkillsBenchAdapterError("; ".join(errors))


def validate_index_with_schema(output_root: Path) -> list[dict[str, object]]:
    rows = validate_bundle_index(output_root)
    index_path = output_root / "index.json"
    errors = _schema_errors(
        read_json_object(index_path),
        DEFAULT_INDEX_SCHEMA,
        str(index_path),
    )
    if errors:
        raise SkillsBenchAdapterError("; ".join(errors))
    return rows


def command_import(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    bundles = import_selected_tasks(
        upstream_root=args.upstream_root,
        output_root=args.output_root,
        policy=policy,
        verify_git=not args.no_git,
    )
    rows = validate_index_with_schema(args.output_root)
    for bundle in bundles:
        validate_with_schemas(bundle.bundle_dir)
    print(
        json.dumps(
            {
                "status": "imported",
                "upstream_commit": policy.upstream.commit,
                "bundle_count": len(rows),
                "bundles": [
                    {
                        "task_id": bundle.task_id,
                        "role": bundle.role,
                        "bundle_digest": bundle.bundle_digest,
                        "parity_status": bundle.parity_status,
                        "path": str(bundle.bundle_dir),
                    }
                    for bundle in bundles
                ],
            },
            sort_keys=True,
        )
    )
    return 0


def command_validate_all(args: argparse.Namespace) -> int:
    rows = validate_index_with_schema(args.output_root)
    for row in rows:
        validate_with_schemas(args.output_root / str(row["bundle_path"]))
    print(f"PASS: validated index and {len(rows)} SkillsBench task bundle(s)")
    return 0


def command_bind_execution(args: argparse.Namespace) -> int:
    report = read_json_object(args.parity_report)
    upstream = read_json_object(args.upstream_evidence)
    normalized = read_json_object(args.normalized_evidence)
    errors = [
        *_schema_errors(upstream, DEFAULT_EXECUTION_SCHEMA, str(args.upstream_evidence)),
        *_schema_errors(
            normalized, DEFAULT_EXECUTION_SCHEMA, str(args.normalized_evidence)
        ),
    ]
    if errors:
        raise SkillsBenchAdapterError("; ".join(errors))
    result = bind_execution_parity(report, upstream, normalized)
    errors = _schema_errors(result, DEFAULT_PARITY_SCHEMA, str(args.output))
    if errors:
        raise SkillsBenchAdapterError("; ".join(errors))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"PASS: execution parity status={result['status']}")
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    subparsers = root.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    import_parser.add_argument("--upstream-root", type=Path, required=True)
    import_parser.add_argument("--output-root", type=Path, required=True)
    import_parser.add_argument("--no-git", action="store_true")
    import_parser.set_defaults(handler=command_import)

    validate_parser = subparsers.add_parser("validate-all")
    validate_parser.add_argument("--output-root", type=Path, required=True)
    validate_parser.set_defaults(handler=command_validate_all)

    bind_parser = subparsers.add_parser("bind-execution")
    bind_parser.add_argument("--parity-report", type=Path, required=True)
    bind_parser.add_argument("--upstream-evidence", type=Path, required=True)
    bind_parser.add_argument("--normalized-evidence", type=Path, required=True)
    bind_parser.add_argument("--output", type=Path, required=True)
    bind_parser.set_defaults(handler=command_bind_execution)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (OSError, json.JSONDecodeError, SkillsBenchAdapterError) as exc:
        print(f"FAIL: SkillsBench adapter: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
