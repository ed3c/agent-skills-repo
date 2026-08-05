#!/usr/bin/env python3
"""Validate autoresearch-composer lifecycle optimization evidence."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import validator


ROOT = Path(
    os.environ.get("AUTORESEARCH_LIFECYCLE_ROOT", Path(__file__).resolve().parents[1])
).resolve()
PROJECT_ROOT = ROOT.parents[1]
SOURCE_SKILL = PROJECT_ROOT / ".claude" / "skills" / "autoresearch-composer" / "SKILL.md"
SOURCE_CASES = PROJECT_ROOT / ".claude" / "skills" / "autoresearch-composer" / "cases.json"
REPO_CASES = ROOT / "skills" / "autoresearch_composer" / "cases.json"
REPO_SKILL = ROOT / "skills" / "autoresearch_composer" / "skills.md"
REFERENCE = ROOT / "skills" / "autoresearch_composer" / "references" / "state_graph.md"
REPORT = ROOT / "openwiki" / "nonofficial" / "autoresearch-composer-lifecycle.md"
PR_GOLDEN = ROOT / "data" / "autoresearch_golden" / "pr_golden_set.json"
NIGHTLY_GOLDEN = ROOT / "data" / "autoresearch_golden" / "nightly_golden_set.jsonl"
TRACE_SAMPLES = ROOT / "data" / "autoresearch_traces" / "local_trace_samples.jsonl"


def require_file(path: Path, failures: list[str]) -> str:
    if not path.is_file():
        failures.append(f"missing file: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    source_required_available = (PROJECT_ROOT / ".claude").exists()
    source_text = require_file(SOURCE_SKILL, failures) if source_required_available else ""
    source_cases_text = require_file(SOURCE_CASES, failures) if source_required_available else ""
    repo_skill_text = require_file(REPO_SKILL, failures)
    reference_text = require_file(REFERENCE, failures)
    report_text = require_file(REPORT, failures)
    if REPO_CASES.is_file():
        try:
            validator.validate_cases(REPO_CASES)
        except Exception as exc:
            failures.append(f"repo cases invalid: {exc}")
    else:
        failures.append(f"missing file: {REPO_CASES}")
    if source_cases_text:
        try:
            source_cases = json.loads(source_cases_text)
            repo_cases = json.loads(REPO_CASES.read_text(encoding="utf-8")) if REPO_CASES.is_file() else []
            if source_cases != repo_cases:
                failures.append("source cases and production repo cases differ")
        except Exception as exc:
            failures.append(f"source cases invalid: {exc}")
    source_required = [
        "Stateful Workflow Nodes",
        "conditional_edge.S3.compressed_context",
        "conditional_edge.S3.missing_domain_term",
        "Behavior Case And A/B Hard Gate",
        "Semantic Truth Actor Routing",
        "technical_equivalent",
        "repo/agent-skills-repo/scripts/check_autoresearch_lifecycle.py",
        "Golden Dataset",
        "LLM-as-a-Judge",
        "local-first trace",
        "cloud disabled by default",
    ] if source_required_available else []
    repo_required = [
        "WHY:",
        "HOW:",
        "WHEN:",
        "WHEN NOT:",
        "references/state_graph.md",
        "Golden Dataset",
        "pytest eval markers",
        "LLM-as-a-Judge",
        "local-first trace",
        "cloud/API judge hooks are present but disabled by default",
    ]
    reference_required = [
        "S1 match",
        "S3 generate",
        "S4 validate",
        "conditional_edge.S4.ablation_not_positive",
        "domain_terms",
        "Semantic Truth Actor Routing",
        "Opus fresh judge",
        "Codex engineering audit",
        "agy findings",
        "candidate / [推論] / human_required",
    ]
    report_required = [
        "Production Gate",
        "A/B ablation is a hard gate",
        "missing Domain terms",
        "Golden Dataset cases",
        "cloud/API judge execution is physically wired but disabled unless explicitly enabled",
    ]
    for literal in source_required:
        if literal not in source_text:
            failures.append(f"source skill missing literal: {literal}")
    for literal in repo_required:
        if literal not in repo_skill_text:
            failures.append(f"repo skill missing literal: {literal}")
    for literal in reference_required:
        if literal not in reference_text:
            failures.append(f"reference missing literal: {literal}")
    for literal in report_required:
        if literal not in report_text:
            failures.append(f"report missing literal: {literal}")
    ablation = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "ablation_engine.py"), "--cases", str(REPO_CASES), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if ablation.returncode != 0:
        failures.append(f"ablation failed: {ablation.stdout}{ablation.stderr}")
    else:
        payload = json.loads(ablation.stdout)
        telemetry = payload["telemetry"]
        if (
            telemetry["case_count"] != 12
            or telemetry["delta"] <= 0.05
            or telemetry["verdict"] != "PASS"
            or telemetry.get("negative_case_count") != 6
            or telemetry.get("negative_with_skill_success_rate") != 1.0
        ):
            failures.append(f"ablation telemetry insufficient: {telemetry}")
    if failures:
        print("FAIL: autoresearch lifecycle validation failed", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 2
    for path in (PR_GOLDEN, NIGHTLY_GOLDEN, TRACE_SAMPLES):
        if not path.is_file():
            print(f"FAIL: missing autoresearch lifecycle data: {path}", file=sys.stderr)
            return 2
    eval_pr = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "eval_autoresearch_composer.py"), "--dataset", str(PR_GOLDEN)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    eval_nightly = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "eval_autoresearch_composer.py"), "--dataset", str(NIGHTLY_GOLDEN)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    trace = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sample_autoresearch_traces.py"), "--trace-file", str(TRACE_SAMPLES)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if (
        eval_pr.returncode != 0
        or "cloud_judge_enabled=false" not in eval_pr.stdout
        or eval_nightly.returncode != 0
        or "PASS: autoresearch eval suite" not in eval_nightly.stdout
        or trace.returncode != 0
        or "PASS: autoresearch trace sampler" not in trace.stdout
    ):
        print("FAIL: autoresearch eval/trace gate failed", file=sys.stderr)
        print(eval_pr.stdout + eval_pr.stderr + eval_nightly.stdout + eval_nightly.stderr + trace.stdout + trace.stderr, file=sys.stderr)
        return 2
    print("PASS: autoresearch lifecycle optimization gate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
