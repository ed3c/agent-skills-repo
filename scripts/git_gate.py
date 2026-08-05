#!/usr/bin/env python3
"""Run local regression gates for skill asset changes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATES = [
    "scripts/validator.py",
    "scripts/validate_skills_baseline.py",
    "scripts/skill_description_linter.py",
    "scripts/validate_progressive_disclosure.py",
    "scripts/validate_goal_constraints.py",
    "scripts/validate_commit_message.py",
    "scripts/github_skill_harvester.py",
    "scripts/synthetic_case_generator.py",
    "scripts/synthetic_case_quality_report.py",
    "scripts/semantic_arbitration_report.py",
    "scripts/interactions_patch_assert_runner.py",
    "scripts/local_regex_runner.py",
    "scripts/benchmark_runner.py",
    "scripts/ablation_engine.py",
    "scripts/llm_judge.py",
    "scripts/check_openwiki.py",
    "scripts/check_wiki_graph_sync.py",
    "scripts/render_lifecycle_openwiki.py",
    "scripts/check_lifecycle_datasets.py",
    "scripts/check_autoresearch_lifecycle.py",
    # scripts/validate_molecular_commit_lineage.py is deliberately NOT gated here.
    # The ledger it reads describes commits that live in the authoring workspace, not in
    # this repository -- this repository has no .git of its own, so find_repo_root() used
    # to climb out to the parent checkout and validate a different repository's history.
    # Worse, GATES invokes every gate with no arguments, and the no-argument path is a
    # schema check that never walks history at all (0.07s vs 19s), so the gate reported
    # PASS while asserting almost nothing. Lineage is validated where the commits are:
    #   python3 scripts/validate_molecular_commit_lineage.py \
    #       --repo-root <authoring-workspace> [--audit-protected-history]
    # The script itself stays in this repo (openwiki.yaml declares it as
    # commit_lineage_validator) and its selftest is asserted by tests/.
    "scripts/no_op_pruner.py",
    "scripts/no_ops_purger.py",
]
EXCLUDED_INPUT_PARTS = {".git", "__pycache__", ".pytest_cache"}


def input_state_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    paths = sorted(
        path
        for path in root.rglob("*")
        if not EXCLUDED_INPUT_PARTS.intersection(path.relative_to(root).parts)
        and path.name != ".DS_Store"
        and not path.name.endswith(".pyc")
        and (path.is_file() or path.is_symlink())
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(str(stat.S_IMODE(info.st_mode)).encode())
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode())
        else:
            digest.update(b"file\0")
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def write_receipt(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def elapsed_ms(started: float) -> int:
    fixed = os.environ.get("GIT_GATE_FIXED_ELAPSED_MS")
    if fixed is not None:
        return int(fixed)
    return round((time.monotonic() - started) * 1000)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    # 產物用的根跟 --repo-root 走;讀來源仍用 ROOT/REPO(腳本位置)。見模組頂端註解。
    global EMIT_ROOT, EMIT_REPO
    EMIT_ROOT = root
    EMIT_REPO = root.parents[1] if root == ROOT else root
    receipt_path = args.receipt.resolve() if args.receipt else None
    if receipt_path and receipt_path.is_relative_to(root):
        parser.error("--receipt must be outside --repo-root so it cannot change the input-state hash")

    input_state = input_state_sha256(root)
    started = time.monotonic()
    records: list[dict[str, object]] = []
    exit_code = 0
    for gate in GATES:
        gate_started = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(root / gate)],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        records.append(
            {
                "command": ["python3", gate],
                "elapsed_ms": elapsed_ms(gate_started),
                "exit_code": result.returncode,
                "gate": gate,
                "stderr": result.stderr,
                "stderr_sha256": hashlib.sha256(result.stderr.encode()).hexdigest(),
                "stdout": result.stdout,
                "stdout_sha256": hashlib.sha256(result.stdout.encode()).hexdigest(),
            }
        )
        if result.returncode != 0:
            print(f"FAIL: gate failed: {gate}", file=sys.stderr)
            exit_code = result.returncode
            break
    if exit_code == 0:
        print("PASS: git gate defenses passed")

    final_input_state = input_state_sha256(root)
    stable = final_input_state == input_state
    if not stable:
        print("FAIL: git gate changed receipt-bound repo inputs", file=sys.stderr)
        exit_code = 125
    if receipt_path:
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        write_receipt(
            receipt_path,
            {
                "elapsed_ms": elapsed_ms(started),
                "expected_gate_count": len(GATES),
                "final_input_state_sha256": final_input_state,
                "gate_count": len(records),
                "gates": records,
                "input_state_sha256": input_state,
                "repo_root": str(root),
                "schema_version": "git-gate-receipt@0.1.0",
                "status": "passed" if exit_code == 0 and len(records) == len(GATES) and stable else "failed",
            },
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
