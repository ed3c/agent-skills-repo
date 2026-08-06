#!/usr/bin/env python3
"""Write or verify canonical Agent Skills exports."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from skill_arena.agent_skills_export import (  # noqa: E402
    AgentSkillsExportError,
    check_exports,
    write_exports,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--source-root", type=Path, default=ROOT / "skills")
    parser.add_argument(
        "--destination-root", type=Path, default=ROOT / "dist/agent-skills"
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=ROOT / "data/agent-skills/export-policy.json",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=ROOT / "contracts/agent-skills-export.schema.json",
    )
    args = parser.parse_args()

    try:
        if args.write:
            exported = write_exports(
                source_root=args.source_root,
                destination_root=args.destination_root,
                policy_path=args.policy,
            )
            names = ", ".join(skill.portable_name for skill in exported)
            print(f"PASS: wrote {len(exported)} Agent Skills exports: {names}")
        else:
            check_exports(
                source_root=args.source_root,
                committed_root=args.destination_root,
                policy_path=args.policy,
                schema_path=args.schema,
            )
            print("PASS: Agent Skills exports are current and conformant")
    except (OSError, AgentSkillsExportError) as exc:
        print(f"FAIL: Agent Skills export: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
