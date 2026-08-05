#!/usr/bin/env python3
"""Run a real agent command against the same cases with and without skill context."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from local_regex_runner import check_expected


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "skills" / "gemini_interactions" / "cases.json"
DEFAULT_SKILL = ROOT / "skills" / "gemini_interactions" / "skills.md"
DEFAULT_ARTIFACTS = ROOT / "artifacts" / "real-driver-ablation"
DEFAULT_THRESHOLD = 0.20


def elapsed_ms(started: float) -> int:
    fixed = os.environ.get("REAL_DRIVER_FIXED_DURATION_MS")
    if fixed is not None:
        return int(fixed)
    return round((time.monotonic() - started) * 1000)


def fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 2


def parse_agent_output(raw: str) -> tuple[str, str | None]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        report = value.get("result") or value.get("report")
        if isinstance(report, str):
            return report, None

    thread_id: str | None = None
    messages: list[str] = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "thread.started" and isinstance(event.get("thread_id"), str):
            thread_id = event["thread_id"]
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            messages.append(item["text"])
    if messages:
        return messages[-1], thread_id
    return raw, thread_id


def parse_agent_argv(agent_cmd: str | None, agent_argv_json: str | None) -> list[str]:
    if agent_argv_json is not None:
        try:
            value = json.loads(agent_argv_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"--agent-argv-json must be a JSON array of strings: {exc}") from exc
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("--agent-argv-json must be a JSON array of strings")
        if not value:
            raise ValueError("--agent-argv-json must not be empty")
        return value
    try:
        return shlex.split(agent_cmd or "")
    except ValueError as exc:
        raise ValueError(f"cannot parse --agent-cmd: {exc}") from exc


def build_argv(agent_argv: list[str], task: str, session_root: Path) -> list[str]:
    argv = []
    for token in agent_argv:
        if token == "{task}":
            argv.append(task)
        elif token == "{session_root}":
            argv.append(str(session_root))
        else:
            argv.append(token)
    return argv


def files_recursively(root: Path) -> list[Path]:
    files: list[Path] = []
    for child in root.iterdir():
        if child.is_symlink():
            continue
        if child.is_dir():
            files.extend(files_recursively(child))
        elif child.is_file():
            files.append(child)
    return files


def resolve_model(thread_id: str | None, session_root: Path) -> tuple[str | None, str | None]:
    if not thread_id or not session_root.is_dir():
        return None, None
    matches = sorted(
        (path for path in files_recursively(session_root) if thread_id in path.name and path.suffix == ".jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in matches:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload", {}) if isinstance(event, dict) else {}
            model = None
            if event.get("type") == "turn_context" and isinstance(payload, dict):
                model = payload.get("model")
            elif payload.get("type") == "thread_settings_applied":
                settings = payload.get("thread_settings", {})
                model = settings.get("model") if isinstance(settings, dict) else None
            if isinstance(model, str) and model:
                return model, str(path)
    return None, None


def invoke(agent_argv: list[str], task: str, session_root: Path, timeout: int) -> dict[str, Any]:
    argv = build_argv(agent_argv, task, session_root)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="real-driver-agent-") as isolated_cwd:
        try:
            completed = subprocess.run(
                argv, cwd=isolated_cwd, capture_output=True, text=True, timeout=timeout, check=False
            )
            raw_stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
            timed_out = False
        except subprocess.TimeoutExpired as exc:
            raw_stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(raw_stdout, bytes):
                raw_stdout = raw_stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            stderr += f"\nagent command timed out after {timeout} seconds"
            exit_code = 124
            timed_out = True
        except OSError as exc:
            raw_stdout = ""
            stderr = f"cannot execute agent command: {exc}"
            exit_code = 127
            timed_out = False
    output, thread_id = parse_agent_output(raw_stdout)
    model, model_receipt = resolve_model(thread_id, session_root)
    return {
        "argv": argv,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "cwd_kind": "ephemeral-temp",
        "stderr": stderr,
        "raw_stdout": raw_stdout,
        "output": output,
        "thread_id": thread_id,
        "resolved_model": model,
        "resolved_model_receipt": model_receipt,
        "duration_ms": elapsed_ms(started),
    }


def skill_prompt(prompt: str, skill_text: str, skill_hash: str, nonce: str) -> str:
    return (
        "Use the following skill context when it applies. Do not mention this wrapper.\n"
        f"SKILL_CONTENT_SHA256={skill_hash}\nEVAL_NONCE={nonce}\n"
        "<skill>\n"
        f"{skill_text}\n"
        "</skill>\n"
        f"<task>\n{prompt}\n</task>"
    )


def resolve_allow_missing(path: Path) -> Path:
    missing: list[str] = []
    cursor = path.absolute()
    while not cursor.exists() and not cursor.is_symlink():
        parent = cursor.parent
        if parent == cursor:
            break
        missing.insert(0, cursor.name)
        cursor = parent
    resolved = cursor.resolve(strict=False)
    return resolved.joinpath(*missing)


def safe_artifact_path(root: Path, *parts: str) -> Path:
    artifacts_root = resolve_allow_missing(root)
    destination = resolve_allow_missing(root.joinpath(*parts))
    if not destination.is_relative_to(artifacts_root):
        raise ValueError(f"artifact path escapes artifacts root: {'/'.join(parts)}")
    return destination


def write_run(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_resumable_run(
    path: Path,
    expected_argv: list[str],
    arm: str,
    case_id: str,
    run_index: int,
    session_root: Path,
) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read existing artifact {path}: {exc}") from exc
    expected = {
        "arm": arm,
        "case_id": case_id,
        "run": run_index,
        "argv": expected_argv,
        "cwd_kind": "ephemeral-temp",
    }
    mismatches = [key for key, value in expected.items() if not isinstance(result, dict) or result.get(key) != value]
    if mismatches:
        raise ValueError(f"existing artifact {path} mismatches: {', '.join(mismatches)}")
    receipt = result.get("resolved_model_receipt")
    receipt_path = Path(receipt) if isinstance(receipt, str) else None
    if (
        receipt_path is None
        or receipt_path.is_symlink()
        or not receipt_path.is_file()
        or not receipt_path.resolve().is_relative_to(session_root.resolve())
        or not isinstance(result.get("resolved_model"), str)
    ):
        raise ValueError(f"existing artifact {path} has invalid model receipt path")
    receipt_model, receipt_match = resolve_model(result.get("thread_id"), session_root)
    if (
        receipt_model != result["resolved_model"]
        or receipt_match is None
        or Path(receipt_match).resolve() != receipt_path.resolve()
    ):
        raise ValueError(f"existing artifact {path} has invalid model receipt binding")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--skill", type=Path, default=DEFAULT_SKILL)
    agent_input = parser.add_mutually_exclusive_group(required=True)
    agent_input.add_argument("--agent-cmd")
    agent_input.add_argument("--agent-argv-json")
    parser.add_argument("--session-root", type=Path, default=Path.home() / ".codex" / "sessions")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        agent_tokens = parse_agent_argv(args.agent_cmd, args.agent_argv_json)
    except ValueError as exc:
        return fail(str(exc))
    if "{task}" not in agent_tokens:
        return fail("--agent-cmd must contain the literal {task} placeholder")
    if agent_tokens and Path(agent_tokens[0]).name == "codex" and "--skip-git-repo-check" not in agent_tokens:
        return fail("codex --agent-cmd requires --skip-git-repo-check because each invocation uses an ephemeral cwd")
    if args.runs < 1:
        return fail("--runs must be at least 1")
    if args.workers < 1:
        return fail("--workers must be at least 1")
    try:
        cases = json.loads(args.cases.read_text(encoding="utf-8"))
        skill_text = args.skill.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError) as exc:
        return fail(f"cannot load inputs: {exc}")
    if not isinstance(cases, list) or not cases:
        return fail("cases JSON must be a non-empty list")

    skill_hash = hashlib.sha256(skill_text.encode()).hexdigest()
    nonce = f"skill-loaded-{skill_hash[:12]}"
    probe_task = "Loader probe: output EVAL_NONCE exactly if it exists in supplied skill context; otherwise output NO_NONCE."
    probe_with = invoke(agent_tokens, skill_prompt(probe_task, skill_text, skill_hash, nonce), args.session_root, args.timeout)
    probe_without = invoke(agent_tokens, probe_task, args.session_root, args.timeout)
    loader_probe_passed = nonce in probe_with["output"] and nonce not in probe_without["output"]
    print(f"PROGRESS loader-probe passed={loader_probe_passed}", file=sys.stderr, flush=True)

    arm_passes: dict[str, list[bool]] = {"with_skill": [], "without_skill": []}
    all_runs: list[dict[str, Any]] = []
    jobs: list[tuple[str, str, int, str, list[str]]] = []
    for arm in ("with_skill", "without_skill"):
        for case in cases:
            case_id = str(case.get("id") or case.get("case_id"))
            for run_index in range(args.runs):
                prompt = str(case["prompt"])
                task = skill_prompt(prompt, skill_text, skill_hash, nonce) if arm == "with_skill" else prompt
                jobs.append((arm, case_id, run_index, task, [str(item) for item in case["expected_checks"]]))

    pending_jobs: list[tuple[str, str, int, str, list[str]]] = []
    for arm, case_id, run_index, task, checks in jobs:
        try:
            path = safe_artifact_path(args.artifacts, arm, case_id, f"run-{run_index}.json")
        except ValueError as exc:
            return fail(str(exc))
        if not args.resume or not path.is_file():
            pending_jobs.append((arm, case_id, run_index, task, checks))
            continue
        try:
            result = load_resumable_run(
                path,
                build_argv(agent_tokens, task, args.session_root),
                arm,
                case_id,
                run_index,
                args.session_root,
            )
        except ValueError as exc:
            return fail(str(exc))
        passed, failures = check_expected(result["output"], checks)
        if result["exit_code"] != 0:
            passed = False
            failures = [f"AGENT_EXIT:{result['exit_code']}", *failures]
        result.update({"passed": passed, "failures": failures})
        arm_passes[arm].append(passed)
        all_runs.append(result)
        print(
            f"PROGRESS arm={arm} case={case_id} run={run_index} "
            f"passed={passed} exit={result['exit_code']} resumed=True",
            file=sys.stderr,
            flush=True,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        pending = {
            executor.submit(invoke, agent_tokens, task, args.session_root, args.timeout): (arm, case_id, run_index, checks)
            for arm, case_id, run_index, task, checks in pending_jobs
        }
        for future in as_completed(pending):
            arm, case_id, run_index, checks = pending[future]
            result = future.result()
            passed, failures = check_expected(result["output"], checks)
            if result["exit_code"] != 0:
                passed = False
                failures = [f"AGENT_EXIT:{result['exit_code']}", *failures]
            result.update({"arm": arm, "case_id": case_id, "run": run_index, "passed": passed, "failures": failures})
            try:
                artifact_path = safe_artifact_path(args.artifacts, arm, case_id, f"run-{run_index}.json")
            except ValueError as exc:
                return fail(str(exc))
            write_run(artifact_path, result)
            print(
                f"PROGRESS arm={arm} case={case_id} run={run_index} "
                f"passed={passed} exit={result['exit_code']} duration_ms={result['duration_ms']}",
                file=sys.stderr,
                flush=True,
            )
            arm_passes[arm].append(passed)
            all_runs.append(result)

    with_rate = sum(arm_passes["with_skill"]) / len(arm_passes["with_skill"])
    without_rate = sum(arm_passes["without_skill"]) / len(arm_passes["without_skill"])
    delta = with_rate - without_rate
    models = sorted({str(run["resolved_model"]) for run in all_runs if run["resolved_model"]})
    metadata_complete = len(models) == 1 and all(run["resolved_model"] == models[0] for run in all_runs)
    agent_failures = sum(1 for run in all_runs if run["exit_code"] != 0)
    probe_failures = sum(1 for run in (probe_with, probe_without) if run["exit_code"] != 0)
    passed = loader_probe_passed and metadata_complete and agent_failures == 0 and probe_failures == 0 and delta >= args.threshold
    report = {
        "schema_version": "real-driver-ablation@0.2.0",
        "inputs": {
            "cases_path": str(args.cases.resolve()),
            "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest(),
            "skill_path": str(args.skill.resolve()),
            "skill_sha256": skill_hash,
            "runs_per_case_per_arm": args.runs,
            "workers": args.workers,
            "threshold": args.threshold,
        },
        "loader_probe": {"passed": loader_probe_passed, "nonce": nonce, "with": probe_with, "without": probe_without},
        "runtime": {
            "resolved_models": models,
            "model_source": "runtime-session-metadata",
            "metadata_complete": metadata_complete,
        },
        "telemetry": {
            "case_count": len(cases),
            "total_case_runs": len(all_runs),
            "with_skill_success_rate": round(with_rate, 4),
            "without_skill_success_rate": round(without_rate, 4),
            "delta": round(delta, 4),
            "agent_failures": agent_failures + probe_failures,
            "verdict": "PASS" if passed else "FAIL",
        },
    }
    try:
        summary_path = safe_artifact_path(args.artifacts, "summary.json")
    except ValueError as exc:
        return fail(str(exc))
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{'PASS' if passed else 'FAIL'}: real-driver delta={delta:.2f} model={models or 'unresolved'} loader_probe={loader_probe_passed}")
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
