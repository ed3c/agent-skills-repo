#!/usr/bin/env python3
"""Check final repo compatibility with the generated plan package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
GIT_GATE_ORDER = [
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
    "scripts/validate_molecular_commit_lineage.py",
    "scripts/no_op_pruner.py",
    "scripts/no_ops_purger.py",
]
GIT_GATE_EXPECTATIONS = {
    "scripts/synthetic_case_generator.py": ("stderr", ["synthetic_cases=117"]),
    "scripts/synthetic_case_quality_report.py": ("stdout", ["quality_status=insufficient", "unique_expected_check_sets=2"]),
    "scripts/semantic_arbitration_report.py": ("stdout", [
        "semantic_arbitration_status=candidate_until_human_admit",
        "agy_execution_lessons=10",
        "agy_model=gemini-3.6-flash-high",
        "agy_thinking=extended",
        "agy_canary=passed-strip-equals",
        "pending_adversarial_reviews=3",
    ]),
    "scripts/interactions_patch_assert_runner.py": ("stdout", ["total_cases_evaluated=117", "zero_llm_api_calls=0"]),
    "scripts/local_regex_runner.py": ("stdout", ["total_trials=50", "zero_llm_api_calls=0"]),
    "scripts/check_openwiki.py": ("stdout", ["PASS: openwiki usage and lifecycle wiring"]),
    "scripts/check_wiki_graph_sync.py": ("stdout", ["PASS: wiki graph sync architecture and artifacts"]),
    "scripts/check_lifecycle_datasets.py": ("stdout", ["PASS: lifecycle datasets and openwiki display"]),
    "scripts/check_autoresearch_lifecycle.py": ("stdout", ["PASS: autoresearch lifecycle optimization gate"]),
    "scripts/validate_molecular_commit_lineage.py": ("stdout", ["PASS: molecular commit lineage"]),
}
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


def load_gate_receipt(path: Path, root: Path) -> dict[str, SimpleNamespace]:
    info = path.lstat()
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError(f"gate receipt must be a private regular file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid gate receipt JSON: {path}") from exc
    gates = payload.get("gates")
    if (
        payload.get("schema_version") != "git-gate-receipt@0.1.0"
        or payload.get("status") != "passed"
        or payload.get("repo_root") != str(root)
        or payload.get("expected_gate_count") != len(GIT_GATE_ORDER)
        or payload.get("gate_count") != len(GIT_GATE_ORDER)
        or not isinstance(gates, list)
        or [gate.get("gate") for gate in gates if isinstance(gate, dict)] != GIT_GATE_ORDER
        or payload.get("input_state_sha256") != payload.get("final_input_state_sha256")
        or payload.get("input_state_sha256") != input_state_sha256(root)
    ):
        raise ValueError("gate receipt contract or input state mismatch")
    results: dict[str, SimpleNamespace] = {}
    for expected, gate in zip(GIT_GATE_ORDER, gates, strict=True):
        if (
            not isinstance(gate, dict)
            or gate.get("command") != ["python3", expected]
            or gate.get("exit_code") != 0
            or not isinstance(gate.get("stdout"), str)
            or not isinstance(gate.get("stderr"), str)
            or gate.get("stdout_sha256") != hashlib.sha256(gate["stdout"].encode()).hexdigest()
            or gate.get("stderr_sha256") != hashlib.sha256(gate["stderr"].encode()).hexdigest()
        ):
            raise ValueError(f"gate receipt entry mismatch: {expected}")
        results[expected] = SimpleNamespace(returncode=0, stdout=gate["stdout"], stderr=gate["stderr"])
    for gate, (stream, literals) in GIT_GATE_EXPECTATIONS.items():
        if any(literal not in getattr(results[gate], stream) for literal in literals):
            raise ValueError(f"gate receipt output mismatch: {gate}")
    return results


def parse_manifest(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if ": " in raw:
            key, value = raw.split(": ", 1)
            data[key] = value
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-receipt", type=Path)
    args = parser.parse_args(argv)
    receipt = load_gate_receipt(args.gate_receipt.absolute(), ROOT) if args.gate_receipt else None
    manifest = parse_manifest(ROOT / "plan-package.compat.yaml")
    required = [
        "PROJECT-SSOT.md",
        ".plan-package.lock.yaml",
        "pyproject.toml",
        ".githooks/pre-push",
        ".githooks/commit-msg",
        ".github/workflows/skill_ci.yml",
        ".github/workflows/autoresearch_eval.yml",
        ".github/workflows/wiki_graph_sync.yml",
        ".github/workflows/weekly_audit.yml",
        "README.md",
        "openwiki/nonofficial/README.md",
        "openwiki/nonofficial/usage.md",
        "openwiki/nonofficial/asset-lifecycle-map.md",
        "openwiki/nonofficial/stateful-workflow.md",
        "openwiki/nonofficial/code-call-lifecycle.md",
        "openwiki/nonofficial/production-bottlenecks.md",
        "openwiki/nonofficial/autoresearch-composer-lifecycle.md",
        "openwiki/nonofficial/structured-lifecycle-data.md",
        "openwiki/nonofficial/prompt-trace-assets.md",
        "openwiki/nonofficial/wiki-graph-sync-architecture.md",
        "openwiki/nonofficial/schema-standards.md",
        "openwiki/nonofficial/openwiki.yaml",
        "scripts/validator.py",
        "scripts/ablation_engine.py",
        "scripts/llm_judge.py",
        "scripts/git_gate.py",
        "scripts/check_openwiki.py",
        "scripts/check_autoresearch_lifecycle.py",
        "scripts/check_lifecycle_datasets.py",
        "scripts/check_prompt_trace_assets.py",
        "scripts/render_lifecycle_openwiki.py",
        "scripts/sync_wiki_to_graph.py",
        "scripts/check_wiki_graph_sync.py",
        "scripts/eval_autoresearch_composer.py",
        "scripts/sample_autoresearch_traces.py",
        "scripts/skill_description_linter.py",
        "scripts/validate_progressive_disclosure.py",
        "scripts/validate_goal_constraints.py",
        "scripts/validate_commit_message.py",
        "scripts/validate_molecular_commit_lineage.py",
        "scripts/github_skill_harvester.py",
        "scripts/synthetic_case_generator.py",
        "scripts/synthetic_case_quality_report.py",
        "scripts/semantic_arbitration_report.py",
        "scripts/interactions_patch_assert_runner.py",
        "scripts/local_regex_runner.py",
        "scripts/benchmark_runner.py",
        "scripts/no_op_pruner.py",
        "data/prompt_trace/prompt_trace_dataset.json",
        "data/prompt_trace/golden_prompt_trace_eval.json",
        "scripts/no_ops_purger.py",
        "data/semantic_arbitration_claims.json",
        "data/agy_execution_experience.json",
        "data/autoresearch_golden/pr_golden_set.json",
        "data/autoresearch_golden/nightly_golden_set.jsonl",
        "data/autoresearch_traces/local_trace_samples.jsonl",
        "data/autoresearch_traces/failure_trace_samples.jsonl",
        "data/lifecycle/skill_optimization_registry.json",
        "data/lifecycle/golden_dataset_versions.json",
        "data/lifecycle/eval_runs/autoresearch_composer_2026-07-23.json",
        "data/lifecycle/promotion_records.json",
        "data/lifecycle/dataset_drift_history.jsonl",
        "data/lifecycle/trace_privacy_classification.json",
        "data/wiki_graph/schema.json",
        "data/wiki_graph/event_log.jsonl",
        "data/wiki_graph/sample_graph.json",
        "data/commit_lineage/gcr_molecular_commits.json",
        "data/verification_runs/gcr_molecular_commit_traceability_2026-07-23.json",
        "data/verification_runs/gcr_three_surface_commit_traceability_2026-07-27.json",
        "skills/gemini_interactions/skills.md",
        "skills/gemini_interactions/cases.json",
        "skills/gemini_interactions/references/deploy_guide.md",
        "skills/autoresearch_composer/skills.md",
        "skills/autoresearch_composer/cases.json",
        "skills/autoresearch_composer/references/state_graph.md",
        "tests/test_autoresearch_eval_suite.py",
        "tests/test_skill_asset_governance.py",
    ]
    missing = [path for path in required if not (ROOT / path).is_file()]
    forbidden = ["small-loop", "packets", "templates/skill-defense-governance"]
    present_forbidden = [path for path in forbidden if (ROOT / path).exists()]
    skill_text = (ROOT / "skills" / "gemini_interactions" / "skills.md").read_text(encoding="utf-8")
    required_terms = ["WHY:", "HOW:", "WHEN:", "WHEN NOT:", "references/deploy_guide.md"]
    missing_terms = [term for term in required_terms if term not in skill_text]
    quality = receipt["scripts/synthetic_case_quality_report.py"] if receipt else subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "synthetic_case_quality_report.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    arbitration = receipt["scripts/semantic_arbitration_report.py"] if receipt else subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "semantic_arbitration_report.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    openwiki = receipt["scripts/check_openwiki.py"] if receipt else subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_openwiki.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    wiki_graph = receipt["scripts/check_wiki_graph_sync.py"] if receipt else subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_wiki_graph_sync.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    lifecycle_datasets = receipt["scripts/check_lifecycle_datasets.py"] if receipt else subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_lifecycle_datasets.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    autoresearch = receipt["scripts/check_autoresearch_lifecycle.py"] if receipt else subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_autoresearch_lifecycle.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    commit_message = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_commit_message.py"), "--selftest"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    molecular_lineage = receipt["scripts/validate_molecular_commit_lineage.py"] if receipt else subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_molecular_commit_lineage.py")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if (
        missing
        or present_forbidden
        or missing_terms
        or manifest.get("final_repo_has_small_loop") != "false"
        or manifest.get("project_archetype") != "skill-asset-governance-repo"
        or manifest.get("project_template_file_count") != "86"
        or manifest.get("molecular_commit_count") != "10"
        or manifest.get("lineage_edge_count") != "92"
        or manifest.get("input_registry") != "inputs/plan-package-inputs.yaml"
        or manifest.get("plan_package_input_count") != "39"
        or manifest.get("plan_package_input_policy") != "registry-order-then-packet-state"
        or manifest.get("autoresearch_eval_status") != "local_first_golden_dataset_guardrail_trace"
        or manifest.get("autoresearch_cloud_judge_policy") != "implemented_disabled_by_default"
        or manifest.get("lifecycle_dataset_status") != "structured_ssot_with_openwiki_display"
        or manifest.get("synthetic_case_quality_status") != "insufficient"
        or manifest.get("p11_current_scope") != "local-zero-llm-regex-canary"
        or manifest.get("semantic_arbitration_status") != "candidate_until_human_admit"
        or manifest.get("adversarial_review_policy") != "codex-executed-agy-required-before-promotion"
        or manifest.get("agy_execution_profile") != "data/agy_execution_experience.json"
        or manifest.get("molecular_commit_lineage_validator") != "scripts/validate_molecular_commit_lineage.py"
        or manifest.get("molecular_commit_lineage_ledger") != "data/commit_lineage/gcr_molecular_commits.json"
        or manifest.get("molecular_commit_lineage_verification_run") != "data/verification_runs/gcr_three_surface_commit_traceability_2026-07-27.json"
        or quality.returncode != 0
        or "quality_status=insufficient" not in quality.stdout
        or arbitration.returncode != 0
        or "semantic_arbitration_status=candidate_until_human_admit" not in arbitration.stdout
        or "pending_adversarial_reviews=" not in arbitration.stdout
        or openwiki.returncode != 0
        or "PASS: openwiki usage and lifecycle wiring" not in openwiki.stdout
        or wiki_graph.returncode != 0
        or "PASS: wiki graph sync architecture and artifacts" not in wiki_graph.stdout
        or lifecycle_datasets.returncode != 0
        or "PASS: lifecycle datasets and openwiki display" not in lifecycle_datasets.stdout
        or autoresearch.returncode != 0
        or "PASS: autoresearch lifecycle optimization gate" not in autoresearch.stdout
        or commit_message.returncode != 0
        or "PASS: commit message traceability contract" not in commit_message.stdout
        or molecular_lineage.returncode != 0
        or "PASS: molecular commit lineage" not in molecular_lineage.stdout
    ):
        if missing:
            print(f"FAIL: missing required path(s): {', '.join(missing)}", file=sys.stderr)
        if present_forbidden:
            print(f"FAIL: final repo contains small-loop path(s): {', '.join(present_forbidden)}", file=sys.stderr)
        if missing_terms:
            print(f"FAIL: skills.md missing required term(s): {', '.join(missing_terms)}", file=sys.stderr)
        if quality.returncode != 0 or "quality_status=insufficient" not in quality.stdout:
            print(f"FAIL: synthetic case quality perception failed: {quality.stdout}{quality.stderr}", file=sys.stderr)
        if arbitration.returncode != 0 or "semantic_arbitration_status=candidate_until_human_admit" not in arbitration.stdout:
            print(f"FAIL: semantic arbitration perception failed: {arbitration.stdout}{arbitration.stderr}", file=sys.stderr)
        if openwiki.returncode != 0 or "PASS: openwiki usage and lifecycle wiring" not in openwiki.stdout:
            print(f"FAIL: openwiki perception failed: {openwiki.stdout}{openwiki.stderr}", file=sys.stderr)
        if wiki_graph.returncode != 0 or "PASS: wiki graph sync architecture and artifacts" not in wiki_graph.stdout:
            print(f"FAIL: wiki graph sync perception failed: {wiki_graph.stdout}{wiki_graph.stderr}", file=sys.stderr)
        if lifecycle_datasets.returncode != 0 or "PASS: lifecycle datasets and openwiki display" not in lifecycle_datasets.stdout:
            print(f"FAIL: lifecycle dataset perception failed: {lifecycle_datasets.stdout}{lifecycle_datasets.stderr}", file=sys.stderr)
        if autoresearch.returncode != 0 or "PASS: autoresearch lifecycle optimization gate" not in autoresearch.stdout:
            print(f"FAIL: autoresearch lifecycle perception failed: {autoresearch.stdout}{autoresearch.stderr}", file=sys.stderr)
        if commit_message.returncode != 0 or "PASS: commit message traceability contract" not in commit_message.stdout:
            print(f"FAIL: commit message perception failed: {commit_message.stdout}{commit_message.stderr}", file=sys.stderr)
        if molecular_lineage.returncode != 0 or "PASS: molecular commit lineage" not in molecular_lineage.stdout:
            print(f"FAIL: molecular commit lineage perception failed: {molecular_lineage.stdout}{molecular_lineage.stderr}", file=sys.stderr)
        return 2
    print("PASS: plan package compatibility")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(2)
