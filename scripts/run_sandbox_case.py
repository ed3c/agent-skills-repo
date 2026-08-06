#!/usr/bin/env python3
"""Run one OpenShell 0.0.59 sandbox case and emit a signed evidence bundle."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skill_arena.sandbox_executor import (  # noqa: E402
    OpenShell059Driver,
    SandboxCase,
    SandboxExecutorError,
    SandboxProfile,
    execute_case_to_bundle,
    load_json_object,
    load_private_key,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--case",
        type=Path,
        default=ROOT / "data/sandbox_cases/smoke-python.json",
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=ROOT
        / "data/sandbox_profiles/openshell-0.0.59-docker.json",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "data/sandbox_profiles/no-network.policy.yaml",
    )
    parser.add_argument(
        "--runner",
        type=Path,
        default=ROOT / "scripts/sandbox_case_runner.py",
    )
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--issuer-key-id", required=True)
    parser.add_argument("--benchmark-suite-digest", required=True)
    parser.add_argument("--skill-artifact-digest", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args()

    try:
        case = SandboxCase.from_mapping(load_json_object(args.case))
        profile = SandboxProfile.from_mapping(load_json_object(args.profile))
        private_key = load_private_key(
            args.private_key,
            repo_root=args.repo_root,
            issuer_key_id=args.issuer_key_id,
        )
        receipt = execute_case_to_bundle(
            case=case,
            profile=profile,
            policy_path=args.policy,
            runner_path=args.runner,
            driver=OpenShell059Driver(),
            private_key=private_key,
            issuer_key_id=args.issuer_key_id,
            output_dir=args.output_dir,
            benchmark_suite_digest=args.benchmark_suite_digest,
            skill_artifact_digest=args.skill_artifact_digest,
            now=datetime.now(timezone.utc),
            run_id=args.run_id,
        )
    except SandboxExecutorError as exc:
        print(f"FAIL[{int(exc.code)}]: {exc}", file=sys.stderr)
        return int(exc.code)
    print(
        f"PASS: sandbox receipt {receipt['receipt_hash']} -> {args.output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
