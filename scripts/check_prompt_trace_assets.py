#!/usr/bin/env python3
"""Validate prompt trace assets, golden evals, and OpenWiki display."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_SLOTS = {"fixed_prompt", "iteration_auto_prompt", "emergent_prompt"}
REQUIRED_ACTORS = {"codex", "agy", "external-verify", "judge-loop-chooser", "openwiki"}


def read_json(relative: str) -> dict[str, object]:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def parse_explicit_roots(args: list[str]) -> tuple[Path | None, Path | None]:
    values: dict[str, Path] = {}
    index = 0
    while index < len(args):
        flag = args[index]
        if flag not in {"--workspace-root", "--commit-repo"}:
            raise ValueError(f"unknown argument: {flag}")
        if flag in values or index + 1 >= len(args) or args[index + 1].startswith("--"):
            raise ValueError(f"{flag} requires exactly one value")
        values[flag] = Path(args[index + 1])
        index += 2

    workspace = values.get("--workspace-root")
    if workspace is not None:
        if (
            not workspace.is_absolute()
            or not workspace.is_dir()
            or not (workspace / "loop_wiki/evolve-unknown-discovery-plan-truth").is_dir()
        ):
            raise ValueError("--workspace-root must be an existing absolute plan-truth workspace")
        workspace = workspace.resolve()

    commit_repo = values.get("--commit-repo")
    if commit_repo is not None:
        if (
            not commit_repo.is_absolute()
            or not commit_repo.is_dir()
            or not (commit_repo / ".git").exists()
        ):
            raise ValueError("--commit-repo must be an existing absolute Git repository")
        commit_repo = commit_repo.resolve()
    return workspace, commit_repo


def main() -> int:
    failures: list[str] = []
    try:
        workspace, explicit_commit_repo = parse_explicit_roots(sys.argv[1:])
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    dataset = read_json("data/prompt_trace/prompt_trace_dataset.json")
    golden = read_json("data/prompt_trace/golden_prompt_trace_eval.json")
    openwiki = (ROOT / "openwiki" / "nonofficial" / "prompt-trace-assets.md").read_text(encoding="utf-8")

    records = dataset.get("records", [])
    if not isinstance(records, list) or len(records) != 3:
        failures.append("prompt trace dataset must contain exactly 3 seed records")
        records = []
    slots = {str(record.get("prompt_slot")) for record in records if isinstance(record, dict)}
    if slots != REQUIRED_SLOTS:
        failures.append(f"prompt slots mismatch: {sorted(slots)}")
    actors = set(dataset.get("selection_actors", []))
    if actors != REQUIRED_ACTORS:
        failures.append("selection actors must include codex, agy, external-verify, judge-loop-chooser, and openwiki")
    privacy = dataset.get("privacy_policy", {})
    if not isinstance(privacy, dict):
        failures.append("privacy_policy must be structured")
        privacy = {}
    if privacy.get("cloud_upload_allowed") is not False:
        failures.append("prompt trace cloud_upload_allowed must be false")
    if privacy.get("raw_external_model_outputs_stored") is not False:
        failures.append("prompt trace must not store raw external model outputs")
    if "human_admit" not in str(privacy.get("model_training_use", "")):
        failures.append("model_training_use must require human admit")

    trace = dataset.get("input_to_terminal_trace", {})
    if not isinstance(trace, dict):
        failures.append("input_to_terminal_trace must be structured")
        trace = {}
    if trace.get("input_id") != "gcr-047d548-conversation":
        failures.append("input trace must bind the primary GCR input id")
    if not re.fullmatch(r"[0-9a-f]{64}", str(trace.get("input_content_sha256", ""))):
        failures.append("input trace must carry a sha256 of frozen input content")
    if "如何實作" not in str(trace.get("input_excerpt", "")):
        failures.append("input trace must expose a bounded source-content excerpt")
    expected_roots = [
        "loop_wiki/evolve-unknown-discovery-plan-truth/",
        "prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/",
        "repo/agent-skills-repo/",
    ]
    if trace.get("directory_roots") != expected_roots:
        failures.append("input trace directory roots must show plan package, small loop, and final repo")
    auto_prompt = trace.get("auto_prompt_snapshot", {})
    if not isinstance(auto_prompt, dict):
        failures.append("auto_prompt_snapshot must be structured")
        auto_prompt = {}
    expected_signals = {
        "packet_state": "measured",
        "next_route_node": "human-admit-readiness",
        "next_conditional_edge": "production-equivalence-improved -> human-admit-surface",
        "missing_production_file_count": 0,
    }
    for field, expected in expected_signals.items():
        if auto_prompt.get(field) != expected:
            failures.append(f"auto prompt snapshot {field} mismatch")
    mappings = trace.get("intent_commit_terminal", [])
    if not isinstance(mappings, list) or len(mappings) != 7:
        failures.append("input trace must expose exactly 7 legacy intent/commit/terminal mappings")
        mappings = []
    expected_slices = {f"GCR-SLICE-{index:02d}" for index in range(1, 8)}
    actual_slices = {str(item.get("intent_slice")) for item in mappings if isinstance(item, dict)}
    if actual_slices != expected_slices:
        failures.append("intent/commit mapping slices must cover GCR-SLICE-01..07")
    for item in mappings:
        if not isinstance(item, dict):
            failures.append("intent/commit mapping must be object")
            continue
        if not re.fullmatch(r"[0-9a-f]{40}", str(item.get("commit_sha", ""))):
            failures.append(f"intent mapping has invalid commit sha: {item.get('intent_slice')}")
        if not str(item.get("commit_repo", "")).strip():
            failures.append(f"intent mapping missing commit_repo: {item.get('intent_slice')}")
        if not item.get("terminal_artifacts_abs"):
            failures.append(f"intent mapping has no terminal artifacts: {item.get('intent_slice')}")

    if workspace is not None:
        frozen_input = workspace / "prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/inputs/frozen/gcr-047d548af8f8e34c-conversation.md"
        if not frozen_input.is_file():
            failures.append(f"frozen primary input missing: {frozen_input}")
        elif hashlib.sha256(frozen_input.read_bytes()).hexdigest() != trace.get("input_content_sha256"):
            failures.append("input trace sha256 does not match frozen small-loop content")

        writer = workspace / "loop_wiki/evolve-unknown-discovery-plan-truth/scripts/write_loop_auto_prompt.py"
        if not writer.is_file():
            failures.append(f"loop auto-prompt writer missing: {writer}")
        else:
            with tempfile.TemporaryDirectory(prefix="prompt-trace-current-") as raw:
                rendered = Path(raw) / "loop-auto-prompt.md"
                result = subprocess.run(
                    [sys.executable, str(writer), "--output", str(rendered)],
                    cwd=workspace,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if result.returncode != 0:
                    failures.append(f"cannot regenerate current loop auto-prompt: {result.stderr.strip()}")
                else:
                    current_prompt = rendered.read_text(encoding="utf-8")
                    for field, expected in expected_signals.items():
                        marker = f"{field}: {str(expected).lower() if isinstance(expected, bool) else expected}"
                        if marker not in current_prompt:
                            failures.append(f"current loop auto-prompt missing measured signal: {marker}")

        for item in mappings:
            if not isinstance(item, dict):
                continue
            artifacts = item.get("terminal_artifacts_abs", [])
            if isinstance(artifacts, list) and not all(Path(str(path)).exists() for path in artifacts):
                failures.append(f"intent mapping terminal artifact missing: {item.get('intent_slice')}")

    for item in mappings:
        if not isinstance(item, dict):
            continue
        sha = str(item.get("commit_sha", ""))
        repo_name = str(item.get("commit_repo", "")).strip()
        commit_root = None
        if workspace is not None and repo_name == workspace.name:
            commit_root = workspace
        elif explicit_commit_repo is not None and repo_name == explicit_commit_repo.name:
            commit_root = explicit_commit_repo
        elif explicit_commit_repo is not None and repo_name:
            failures.append(
                "explicit commit repo name mismatch: "
                f"expected {repo_name}, got {explicit_commit_repo.name}"
            )
        if commit_root is not None:
            subject = subprocess.run(
                ["git", "show", "-s", "--format=%s", sha],
                cwd=commit_root,
                text=True,
                capture_output=True,
                check=False,
            )
            if subject.returncode != 0 or subject.stdout.strip() != item.get("subject"):
                failures.append(
                    f"intent mapping Git subject mismatch: {item.get('intent_slice')} in {repo_name}"
                )

    for record in records:
        if not isinstance(record, dict):
            failures.append("prompt trace record must be object")
            continue
        for key in ("trace_id", "prompt_slot", "source_path", "route", "state_node", "typescript_target", "evidence_gate", "training_eligibility"):
            if not str(record.get(key, "")).strip():
                failures.append(f"prompt trace record missing {key}: {record.get('trace_id')}")
        if str(record.get("evidence_gate")) != "scripts/check_prompt_trace_assets.py":
            failures.append(f"prompt trace evidence gate mismatch: {record.get('trace_id')}")
        if record.get("training_eligibility") != "metadata-only-local":
            failures.append(f"prompt trace training eligibility mismatch: {record.get('trace_id')}")

    cases = golden.get("cases", [])
    if not isinstance(cases, list) or len(cases) != 3:
        failures.append("golden prompt trace eval must contain exactly 3 cases")
        cases = []
    case_ids = {str(case.get("case_id")) for case in cases if isinstance(case, dict)}
    expected_cases = {"prompt-slot-separation", "adversarial-selection-contract", "training-privacy-contract"}
    if case_ids != expected_cases:
        failures.append(f"golden prompt trace cases mismatch: {sorted(case_ids)}")
    for case in cases:
        if not isinstance(case, dict):
            failures.append("golden prompt trace case must be object")
            continue
        if case.get("verdict") != "PASS":
            failures.append(f"golden prompt trace case must be PASS: {case.get('case_id')}")
        if case.get("case_id") == "training-privacy-contract":
            if case.get("expected_cloud_upload_allowed") is not False:
                failures.append("training privacy case must keep cloud upload disabled")
            if case.get("expected_raw_external_model_outputs_stored") is not False:
                failures.append("training privacy case must forbid raw external outputs")

    required_literals = [
        "Prompt Trace Assets",
        "data/prompt_trace/prompt_trace_dataset.json",
        "data/prompt_trace/golden_prompt_trace_eval.json",
        "scripts/check_prompt_trace_assets.py",
        "fixed_prompt",
        "iteration_auto_prompt",
        "emergent_prompt",
        "codex",
        "agy",
        "external-verify",
        "judge-loop-chooser",
        "metadata-local prompt traces",
        "Cloud upload and model training use require a later human-admit record.",
        "Directory Structure and Dataflow",
        "flowchart LR",
        "Input Content Through the Small Loop",
        "next_route_node: human-admit-readiness",
        "Intent → Git Commit → Terminal Implementation",
        "git log --all --format='%H%x09%s%x09%b' --grep='Intent-Slice: GCR-SLICE-'",
    ]
    for literal in required_literals:
        if literal not in openwiki:
            failures.append(f"prompt trace openwiki missing literal: {literal}")

    if failures:
        print("FAIL: prompt trace assets validation failed", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 2
    print("PASS: prompt trace assets and openwiki display")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
