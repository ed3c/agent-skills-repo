#!/usr/bin/env python3
"""Validate the compensating lineage ledger for GCR molecular commits."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "molecular-commit-lineage@0.1.0"
THREE_SURFACE_SCHEMA_VERSION = "molecular-commit-lineage@0.2.0"
VERIFICATION_SCHEMA_VERSION = "molecular-commit-verification-run@0.1.0"
SOURCE_CONVERSATION = "<home>/antigravity/gemini_research/gcr/047d548af8f8e34c-conversation.md"
MATERIALIZATION_ROUTE = (
    "<host-repo>/loop_wiki/evolve-unknown-discovery-plan-truth/"
    "ROUTES.md#plan-package-materialization"
)
EXCHANGE_FORMAT = (
    "<host-repo>/loop_wiki/evolve-unknown-discovery-plan-truth/"
    "modules/exchange-formats.md"
)
PLAN_PACKAGE = "<host-repo>/loop_wiki/evolve-unknown-discovery-plan-truth/plan-package.yaml"
EXPECTED_COMMIT_COUNT = 7
REQUIRED_ENTRY_FIELDS = (
    "commit_sha",
    "subject",
    "intent_slice",
    "route_abs",
    "plan_package_abs",
    "exchange_format_abs",
    "exchange_packet_abs",
    "fixed_prompt_context_abs",
    "iteration_auto_context_abs",
    "emergent_prompt_context_abs",
    "terminal_artifacts_abs",
    "changed_files_git_command",
    "changed_file_count",
    "message_status",
    "compensation_status",
    "verification_run_abs",
    "dataflow_abs",
)


def find_repo_root(start: Path) -> Path | None:
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def artifact_root(script_path: Path) -> Path:
    project_root = script_path.resolve().parents[1]
    if (project_root / "data").is_dir() and project_root.name == "agent-skills-repo":
        return project_root
    nested = project_root / "repo" / "agent-skills-repo"
    if (nested / "data").is_dir():
        return nested
    return project_root


def default_ledger_path(script_path: Path) -> Path:
    return artifact_root(script_path) / "data" / "commit_lineage" / "gcr_molecular_commits.json"


def load_json(path: Path) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid molecular lineage JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"molecular lineage JSON must be an object: {path}")
    return payload


def git_output(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def message_field(text: str, field: str) -> str | None:
    match = re.search(rf"^{re.escape(field)}\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def message_has_three_surface_lineage(text: str, payload: dict[str, Any]) -> bool:
    required = (
        "Intent-Slice:",
        "Route:",
        "Plan-Package:",
        "Small-Loop:",
        "Final-Repo:",
        "Exchange-Format:",
        "Exchange-Packet:",
        "Fixed-Prompt-Context:",
        "Iteration-Auto-Context:",
        "Emergent-Prompt-Context:",
        "Dataflow:",
    )
    return (
        all(field in text for field in required)
        and re.search(
            r"^Intent-Slice:\s+(?:GCR-SLICE-\d{2}|TS-SLICE-[A-Z0-9-]+|GOLDEN-FLOW-SLICE-[A-Z0-9-]+)$",
            text,
            flags=re.MULTILINE,
        )
        is not None
        and message_field(text, "Plan-Package:") == payload.get("plan_package_abs")
        and message_field(text, "Small-Loop:") == payload.get("small_loop_abs")
        and message_field(text, "Final-Repo:") == payload.get("final_repo_abs")
    )


def touched_protected_paths(changed_files: list[str], protected_paths: list[str]) -> list[str]:
    return sorted(
        protected
        for protected in protected_paths
        if any(
            path.startswith(protected) if protected.endswith("/") else path == protected
            for path in changed_files
        )
    )


def validate_three_surface_verification(
    payload: dict[str, Any], compensations: list[dict[str, Any]], failures: list[str]
) -> None:
    raw_path = payload.get("verification_run_abs")
    if not isinstance(raw_path, str) or not Path(raw_path).is_file():
        failures.append(f"missing three-surface verification run: {raw_path}")
        return
    try:
        verification = load_json(Path(raw_path))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read three-surface verification run: {exc}")
        return
    shas = [entry.get("commit_sha") for entry in compensations]
    expected = {
        "schema_version": "molecular-commit-verification-run@0.2.0",
        "status": "pass",
        "coverage_through": payload.get("coverage_through"),
        "commit_count": len(compensations),
        "compensated_commit_count": len(compensations),
        "failed_commit_count": 0,
        "protected_paths": payload.get("protected_paths"),
        "commit_shas": shas,
    }
    for field, value in expected.items():
        if verification.get(field) != value:
            failures.append(f"three-surface verification {field} mismatch")
    strict_message_count = verification.get("strict_message_count")
    if (
        not isinstance(strict_message_count, int)
        or isinstance(strict_message_count, bool)
        or strict_message_count < 0
    ):
        failures.append("three-surface verification strict_message_count must be a non-negative integer")
    for field in ("validator_abs", "ledger_abs", "proof_commands"):
        if not verification.get(field):
            failures.append(f"three-surface verification missing {field}")


def validate_three_surface_ledger(
    path: Path,
    payload: dict[str, Any],
    repo_root: Path | None,
    audit_protected_history: bool,
) -> list[str]:
    failures: list[str] = []
    protected_paths = payload.get("protected_paths")
    if not isinstance(protected_paths, list) or not protected_paths or any(
        not isinstance(value, str) or not value for value in protected_paths
    ):
        failures.append("protected_paths must be a non-empty list of path prefixes or exact files")
        return failures
    for field in ("plan_package_abs", "small_loop_abs", "final_repo_abs"):
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            failures.append(f"missing {field}")
    if payload.get("compensation_intent_slice") != "GCR-SLICE-09":
        failures.append("compensation_intent_slice must be GCR-SLICE-09")
    compensations = payload.get("compensated_commits")
    if not isinstance(compensations, list):
        failures.append("compensated_commits must be a list")
        return failures
    valid_compensations: list[dict[str, Any]] = []
    by_sha: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(compensations, 1):
        if not isinstance(entry, dict):
            failures.append(f"compensated_commits[{index}] must be an object")
            continue
        valid_compensations.append(entry)
        sha = entry.get("commit_sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            failures.append(f"compensated_commits[{index}] has invalid commit_sha")
            continue
        if sha in by_sha:
            failures.append(f"duplicate compensated commit: {sha}")
        by_sha[sha] = entry
    validate_three_surface_verification(payload, valid_compensations, failures)
    if not audit_protected_history:
        return failures
    if repo_root is None:
        return [*failures, "--audit-protected-history requires --repo-root or a discoverable Git root"]
    try:
        shas = [
            line
            for line in git_output(repo_root, ["log", "--format=%H", "--", *protected_paths]).splitlines()
            if line
        ]
    except RuntimeError as exc:
        return [*failures, f"cannot enumerate protected history: {exc}"]
    coverage_through = payload.get("coverage_through")
    try:
        coverage_index = shas.index(coverage_through)
    except ValueError:
        rendered = coverage_through if isinstance(coverage_through, str) and coverage_through else "<missing>"
        return [*failures, f"coverage_through is not in protected history: {rendered}"]

    for sha in shas[:coverage_index]:
        try:
            message = git_output(repo_root, ["show", "-s", "--format=%B", sha])
            if not message_has_three_surface_lineage(message, payload):
                subject = git_output(repo_root, ["show", "-s", "--format=%s", sha]).strip()
                failures.append(
                    "post-coverage protected-surface commit lacks strict three-surface lineage: "
                    f"{sha} {subject}"
                )
        except RuntimeError as exc:
            failures.append(f"{sha}: git verification failed: {exc}")

    seen_compensations: set[str] = set()
    strict_message_count = 0
    for sha in shas[coverage_index:]:
        try:
            subject = git_output(repo_root, ["show", "-s", "--format=%s", sha]).strip()
            message = git_output(repo_root, ["show", "-s", "--format=%B", sha])
            changed_files = [
                line
                for line in git_output(repo_root, ["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", sha]).splitlines()
                if line
            ]
        except RuntimeError as exc:
            failures.append(f"{sha}: git verification failed: {exc}")
            continue
        touched = touched_protected_paths(changed_files, protected_paths)
        if message_has_three_surface_lineage(message, payload):
            strict_message_count += 1
            continue
        entry = by_sha.get(sha)
        if entry is None:
            failures.append(f"uncovered protected-surface commit: {sha} {subject}")
            continue
        seen_compensations.add(sha)
        expected = {
            "subject": subject,
            "protected_paths": touched,
            "plan_package_abs": payload.get("plan_package_abs"),
            "small_loop_abs": payload.get("small_loop_abs"),
            "final_repo_abs": payload.get("final_repo_abs"),
            "compensation_intent_slice": payload.get("compensation_intent_slice"),
            "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
        }
        for field, value in expected.items():
            if entry.get(field) != value:
                failures.append(f"{sha}: compensation {field} mismatch")
        if not isinstance(entry.get("reason"), str) or not entry["reason"].strip():
            failures.append(f"{sha}: compensation reason is required")
    stale = sorted(set(by_sha) - seen_compensations)
    failures.extend(f"stale compensated commit not in protected history: {sha}" for sha in stale)
    try:
        verification = load_json(Path(str(payload.get("verification_run_abs"))))
    except (OSError, json.JSONDecodeError) as exc:
        failures.append(f"cannot read three-surface verification run: {exc}")
    else:
        if verification.get("strict_message_count") != strict_message_count:
            failures.append("three-surface verification strict_message_count mismatch")
    return failures


def validate_verification_run(path: Path, expected_shas: list[str], failures: list[str]) -> None:
    if not path.is_file():
        failures.append(f"missing verification run: {path}")
        return
    payload = load_json(path)
    if payload.get("schema_version") != VERIFICATION_SCHEMA_VERSION:
        failures.append(f"{path}: schema_version must be {VERIFICATION_SCHEMA_VERSION}")
    if payload.get("status") != "pass":
        failures.append(f"{path}: status must be pass")
    if payload.get("commit_count") != len(expected_shas):
        failures.append(f"{path}: commit_count mismatch")
    if payload.get("passed_commit_count") != len(expected_shas):
        failures.append(f"{path}: passed_commit_count mismatch")
    if payload.get("failed_commit_count") != 0:
        failures.append(f"{path}: failed_commit_count must be 0")
    if payload.get("commit_shas") != expected_shas:
        failures.append(f"{path}: commit_shas mismatch")
    for key in ("validator_abs", "ledger_abs", "proof_commands"):
        if not payload.get(key):
            failures.append(f"{path}: missing {key}")


def validate_ledger(
    path: Path,
    require_current_history: bool,
    *,
    repo_root: Path | None = None,
    audit_protected_history: bool = False,
) -> list[str]:
    failures: list[str] = []
    if not path.is_file():
        return [f"ledger does not exist: {path}"]
    payload = load_json(path)
    if payload.get("schema_version") == THREE_SURFACE_SCHEMA_VERSION:
        return validate_three_surface_ledger(
            path,
            payload,
            repo_root,
            audit_protected_history or require_current_history,
        )
    if audit_protected_history:
        return ["protected-history audit requires molecular-commit-lineage@0.2.0"]
    if payload.get("schema_version") != SCHEMA_VERSION:
        failures.append(f"schema_version must be {SCHEMA_VERSION}")
    if payload.get("source_conversation_abs") != SOURCE_CONVERSATION:
        failures.append("source_conversation_abs must reference the original GCR conversation")
    if payload.get("materialization_route_abs") != MATERIALIZATION_ROUTE:
        failures.append("materialization_route_abs must be the canonical plan-package route")
    if payload.get("exchange_format_abs") != EXCHANGE_FORMAT:
        failures.append("exchange_format_abs must be modules/exchange-formats.md")
    if payload.get("plan_package_abs") != PLAN_PACKAGE:
        failures.append("plan_package_abs must be the small-loop plan-package.yaml")

    commits = payload.get("commits")
    if not isinstance(commits, list):
        failures.append("commits must be a list")
        return failures
    if len(commits) != EXPECTED_COMMIT_COUNT:
        failures.append(f"commits must contain {EXPECTED_COMMIT_COUNT} entries")

    repo_root = find_repo_root(Path.cwd()) or find_repo_root(path.resolve())
    expected_shas: list[str] = []
    seen_shas: set[str] = set()
    for index, entry in enumerate(commits, 1):
        if not isinstance(entry, dict):
            failures.append(f"commits[{index}] must be an object")
            continue
        missing = [field for field in REQUIRED_ENTRY_FIELDS if field not in entry]
        if missing:
            failures.append(f"{entry.get('commit_sha', index)} missing field(s): {', '.join(missing)}")
            continue
        sha = str(entry["commit_sha"])
        expected_shas.append(sha)
        if sha in seen_shas:
            failures.append(f"duplicate commit_sha: {sha}")
        seen_shas.add(sha)
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            failures.append(f"{sha}: commit_sha must be 40 lowercase hex chars")
        if not re.fullmatch(r"GCR-SLICE-\d{2}", str(entry["intent_slice"])):
            failures.append(f"{sha}: intent_slice must be GCR-SLICE-XX")
        for field, expected in (
            ("route_abs", MATERIALIZATION_ROUTE),
            ("plan_package_abs", PLAN_PACKAGE),
            ("exchange_format_abs", EXCHANGE_FORMAT),
        ):
            if entry.get(field) != expected:
                failures.append(f"{sha}: {field} must be {expected}")
        if entry.get("message_status") != "compensated-human-readable":
            failures.append(f"{sha}: message_status must be compensated-human-readable")
        if entry.get("compensation_status") != "lineage-ledger-admitted":
            failures.append(f"{sha}: compensation_status must be lineage-ledger-admitted")
        if int(entry.get("changed_file_count", 0)) <= 0:
            failures.append(f"{sha}: changed_file_count must be positive")
        fixed = entry.get("fixed_prompt_context_abs")
        terminal = entry.get("terminal_artifacts_abs")
        dataflow = entry.get("dataflow_abs")
        if not isinstance(fixed, list) or len(fixed) < 3:
            failures.append(f"{sha}: fixed_prompt_context_abs must contain at least 3 paths")
        if not isinstance(terminal, list) or not terminal:
            failures.append(f"{sha}: terminal_artifacts_abs must not be empty")
        if not isinstance(dataflow, list) or len(dataflow) < 8:
            failures.append(f"{sha}: dataflow_abs must contain source-to-verifier path")
        path_fields: list[str] = [
            str(entry["exchange_packet_abs"]),
            str(entry["iteration_auto_context_abs"]),
            str(entry["emergent_prompt_context_abs"]),
            str(entry["verification_run_abs"]),
        ]
        path_fields.extend(str(item) for item in fixed if isinstance(fixed, list))
        path_fields.extend(str(item) for item in terminal if isinstance(terminal, list))
        for value in path_fields:
            physical_value = value.split("#", 1)[0]
            physical = Path(physical_value)
            if physical.is_absolute() and not physical.exists():
                failures.append(f"{sha}: referenced path does not exist: {value}")

        if require_current_history:
            if repo_root is None:
                failures.append("cannot find git root for --require-current-history")
                continue
            try:
                subject = git_output(repo_root, ["log", "-1", "--format=%s", sha]).strip()
                changed_files = [
                    line
                    for line in git_output(repo_root, ["show", "--name-only", "--format=", sha]).splitlines()
                    if line.strip()
                ]
            except RuntimeError as exc:
                failures.append(f"{sha}: git verification failed: {exc}")
                continue
            if subject != entry["subject"]:
                failures.append(f"{sha}: subject mismatch: {subject!r}")
            if len(changed_files) != entry["changed_file_count"]:
                failures.append(
                    f"{sha}: changed_file_count mismatch: ledger={entry['changed_file_count']} git={len(changed_files)}"
                )

    verification = payload.get("verification_run_abs")
    if not verification:
        failures.append("missing top-level verification_run_abs")
    else:
        validate_verification_run(Path(str(verification)), expected_shas, failures)
    return failures


def selftest() -> int:
    with tempfile.TemporaryDirectory(prefix="molecular-lineage-") as tmp:
        root = Path(tmp)
        required_paths = [
            root / "loop_wiki/evolve-unknown-discovery-plan-truth/ROUTES.md",
            root / "loop_wiki/evolve-unknown-discovery-plan-truth/modules/exchange-formats.md",
            root / "loop_wiki/evolve-unknown-discovery-plan-truth/plan-package.yaml",
            root / "loop_wiki/evolve-unknown-discovery-plan-truth/packets/outbox/plan-package-materialization-agent-skills-repo.yaml",
            root / "loop_wiki/evolve-unknown-discovery-plan-truth/PROMPT.md",
            root / "loop_wiki/evolve-unknown-discovery-plan-truth/modules/semantic-truth-context.md",
            root / "loop_wiki/evolve-unknown-discovery-plan-truth/modules/development-standards.md",
            root / "docs/plans/2026-07-22-unknown-discovery-gcr-order/invariants/agent-skills-repo/2026-07-23-draft-quality-feedback.md",
            root / "loop_wiki/evolve-unknown-discovery-plan-truth/data/agy-replay-execution-report.json",
            root / "repo/agent-skills-repo/README.md",
        ]
        for item in required_paths:
            item.parent.mkdir(parents=True, exist_ok=True)
            item.write_text("x\n", encoding="utf-8")
        verification = root / "repo/agent-skills-repo/data/verification_runs/example.json"
        ledger = root / "repo/agent-skills-repo/data/commit_lineage/gcr_molecular_commits.json"
        shas = [f"{index:040x}" for index in range(1, EXPECTED_COMMIT_COUNT + 1)]
        verification.parent.mkdir(parents=True, exist_ok=True)
        verification.write_text(
            json.dumps(
                {
                    "schema_version": VERIFICATION_SCHEMA_VERSION,
                    "run_id": "selftest",
                    "status": "pass",
                    "commit_count": EXPECTED_COMMIT_COUNT,
                    "passed_commit_count": EXPECTED_COMMIT_COUNT,
                    "failed_commit_count": 0,
                    "validator_abs": str(root / "repo/agent-skills-repo/scripts/validate_molecular_commit_lineage.py"),
                    "ledger_abs": str(ledger),
                    "proof_commands": ["python3 scripts/validate_molecular_commit_lineage.py"],
                    "commit_shas": shas,
                }
            ),
            encoding="utf-8",
        )
        commits = []
        for index, sha in enumerate(shas, 1):
            commits.append(
                {
                    "commit_sha": sha,
                    "subject": f"selftest {index}",
                    "intent_slice": f"GCR-SLICE-{index:02d}",
                    "route_abs": MATERIALIZATION_ROUTE,
                    "plan_package_abs": PLAN_PACKAGE,
                    "exchange_format_abs": EXCHANGE_FORMAT,
                    "exchange_packet_abs": str(required_paths[3]),
                    "fixed_prompt_context_abs": [str(path) for path in required_paths[4:7]],
                    "iteration_auto_context_abs": str(required_paths[7]),
                    "emergent_prompt_context_abs": str(required_paths[8]),
                    "terminal_artifacts_abs": [str(required_paths[9])],
                    "changed_files_git_command": f"git show --name-only --format= {sha}",
                    "changed_file_count": 1,
                    "message_status": "compensated-human-readable",
                    "compensation_status": "lineage-ledger-admitted",
                    "verification_run_abs": str(verification),
                    "dataflow_abs": [
                        SOURCE_CONVERSATION,
                        str(required_paths[4]),
                        MATERIALIZATION_ROUTE,
                        EXCHANGE_FORMAT,
                        PLAN_PACKAGE,
                        str(required_paths[3]),
                        str(required_paths[9]),
                        str(verification),
                    ],
                }
            )
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "source_conversation_abs": SOURCE_CONVERSATION,
                    "materialization_route_abs": MATERIALIZATION_ROUTE,
                    "exchange_format_abs": EXCHANGE_FORMAT,
                    "plan_package_abs": PLAN_PACKAGE,
                    "verification_run_abs": str(verification),
                    "commits": commits,
                }
            ),
            encoding="utf-8",
        )
        failures = validate_ledger(ledger, require_current_history=False)
        if failures:
            print("FAIL: selftest ledger should pass", file=sys.stderr)
            print("\n".join(failures), file=sys.stderr)
            return 2
        commits[0]["message_status"] = "unverified"
        ledger.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "source_conversation_abs": SOURCE_CONVERSATION,
                    "materialization_route_abs": MATERIALIZATION_ROUTE,
                    "exchange_format_abs": EXCHANGE_FORMAT,
                    "plan_package_abs": PLAN_PACKAGE,
                    "verification_run_abs": str(verification),
                    "commits": commits,
                }
            ),
            encoding="utf-8",
        )
        if not validate_ledger(ledger, require_current_history=False):
            print("FAIL: hollow selftest ledger unexpectedly passed", file=sys.stderr)
            return 2
    print("PASS: molecular commit lineage selftest")
    print("SELFTEST GREEN")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ledger", nargs="?", type=Path, default=default_ledger_path(Path(__file__)))
    parser.add_argument("--require-current-history", action="store_true")
    parser.add_argument("--audit-protected-history", action="store_true")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        return selftest()
    repo_root = args.repo_root or find_repo_root(Path.cwd()) or find_repo_root(args.ledger.resolve())
    try:
        failures = validate_ledger(
            args.ledger,
            args.require_current_history,
            repo_root=repo_root,
            audit_protected_history=args.audit_protected_history,
        )
    except Exception as exc:
        print(f"FAIL: molecular commit lineage validation error: {exc}", file=sys.stderr)
        return 2
    if failures:
        print("FAIL: molecular commit lineage validation failed", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 2
    print("PASS: molecular commit lineage")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
