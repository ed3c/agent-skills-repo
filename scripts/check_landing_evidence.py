#!/usr/bin/env python3
"""Validate the repository-local authority for completed roadmap work."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skill_arena.landing_evidence import (  # noqa: E402
    LandingEvidenceError,
    validate_landing_evidence,
)
from skill_arena.landing_evidence_fragments import (  # noqa: E402
    load_landing_evidence_bundle,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "data/project/landing-evidence.json",
    )
    parser.add_argument(
        "--fragments-dir",
        type=Path,
        default=ROOT / "data/project/landing-evidence.d",
        help="sorted upsert fragments; absent directory is allowed",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=ROOT / "contracts/landing-evidence.schema.json",
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--main-ref")
    parser.add_argument(
        "--no-git",
        action="store_true",
        help="validate schema and digests only; never valid as delivery proof",
    )
    args = parser.parse_args()

    try:
        manifest = load_landing_evidence_bundle(
            args.manifest,
            args.fragments_dir,
        )
        schema = json.loads(args.schema.read_text(encoding="utf-8"))
        validate_landing_evidence(
            manifest,
            schema,
            repo_root=args.repo_root,
            main_ref=args.main_ref,
            verify_git=not args.no_git,
        )
    except (OSError, json.JSONDecodeError, LandingEvidenceError) as exc:
        print(f"FAIL: landing evidence is invalid: {exc}", file=sys.stderr)
        return 2

    mode = "digest-only" if args.no_git else "main-reachability"
    print(f"PASS: landing evidence authority ({mode})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
