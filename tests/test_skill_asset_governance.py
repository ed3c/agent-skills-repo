from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_script(relative: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / relative), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_skill_asset_structure() -> None:
    required = [
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
        "skills/gemini_interactions/skills.md",
        "skills/gemini_interactions/cases.json",
        "skills/gemini_interactions/references/deploy_guide.md",
        "scripts/validate_goal_constraints.py",
        "scripts/validate_commit_message.py",
        "scripts/validate_molecular_commit_lineage.py",
        "scripts/github_skill_harvester.py",
        "scripts/check_openwiki.py",
        "scripts/check_autoresearch_lifecycle.py",
        "scripts/check_lifecycle_datasets.py",
        "scripts/check_prompt_trace_assets.py",
        "scripts/render_lifecycle_openwiki.py",
        "scripts/sync_wiki_to_graph.py",
        "scripts/check_wiki_graph_sync.py",
        "scripts/eval_autoresearch_composer.py",
        "scripts/sample_autoresearch_traces.py",
        "scripts/synthetic_case_generator.py",
        "scripts/interactions_patch_assert_runner.py",
        "scripts/local_regex_runner.py",
        "scripts/benchmark_runner.py",
        "scripts/no_ops_purger.py",
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
        "data/prompt_trace/prompt_trace_dataset.json",
        "data/prompt_trace/golden_prompt_trace_eval.json",
        "data/wiki_graph/schema.json",
        "data/wiki_graph/event_log.jsonl",
        "data/wiki_graph/sample_graph.json",
        "data/commit_lineage/gcr_molecular_commits.json",
        "data/verification_runs/gcr_molecular_commit_traceability_2026-07-23.json",
        "tests/test_autoresearch_eval_suite.py",
    ]
    for path in required:
        assert (ROOT / path).is_file(), path


def test_static_defense_scripts_pass() -> None:
    for script in [
        "scripts/validator.py",
        "scripts/validate_skills_baseline.py",
        "scripts/skill_description_linter.py",
        "scripts/validate_progressive_disclosure.py",
        "scripts/validate_goal_constraints.py",
        "scripts/validate_commit_message.py",
        "scripts/validate_molecular_commit_lineage.py",
        "scripts/github_skill_harvester.py",
        "scripts/synthetic_case_generator.py",
        "scripts/interactions_patch_assert_runner.py",
        "scripts/local_regex_runner.py",
        "scripts/benchmark_runner.py",
        "scripts/ablation_engine.py",
        "scripts/llm_judge.py",
        "scripts/eval_autoresearch_composer.py",
        "scripts/sample_autoresearch_traces.py",
        "scripts/check_openwiki.py",
        "scripts/check_autoresearch_lifecycle.py",
        "scripts/check_lifecycle_datasets.py",
        "scripts/check_prompt_trace_assets.py",
        "scripts/render_lifecycle_openwiki.py",
        "scripts/check_wiki_graph_sync.py",
        "scripts/git_gate.py",
        "scripts/no_op_pruner.py",
        "scripts/no_ops_purger.py",
    ]:
        result = run_script(script)
        assert result.returncode == 0, result.stderr + result.stdout


def test_p11_synthetic_and_zero_llm_telemetry() -> None:
    synthetic = run_script("scripts/synthetic_case_generator.py")
    assert synthetic.returncode == 0, synthetic.stderr + synthetic.stdout
    assert "synthetic_cases=117" in synthetic.stderr
    assert "typescript=59" in synthetic.stderr
    assert "python=58" in synthetic.stderr

    interactions = run_script("scripts/interactions_patch_assert_runner.py")
    assert interactions.returncode == 0, interactions.stderr + interactions.stdout
    assert "total_cases_evaluated=117" in interactions.stdout
    assert "passed_cases=117" in interactions.stdout
    assert "zero_llm_api_calls=0" in interactions.stdout

    local_regex = run_script("scripts/local_regex_runner.py")
    assert local_regex.returncode == 0, local_regex.stderr + local_regex.stdout
    assert "case_count=10" in local_regex.stdout
    assert "total_trials=50" in local_regex.stdout
    assert "zero_llm_api_calls=0" in local_regex.stdout

    benchmark = run_script("scripts/benchmark_runner.py")
    assert benchmark.returncode == 0, benchmark.stderr + benchmark.stdout
    assert "task_count=100" in benchmark.stdout
    assert "delta_high_quality=0.15" in benchmark.stdout
    assert "delta_low_quality=-0.2" in benchmark.stdout


def test_autoresearch_eval_and_trace_gates_pass() -> None:
    pr_eval = run_script("scripts/eval_autoresearch_composer.py")
    assert pr_eval.returncode == 0, pr_eval.stderr + pr_eval.stdout
    assert "cloud_judge_enabled=false" in pr_eval.stdout

    trace = run_script("scripts/sample_autoresearch_traces.py")
    assert trace.returncode == 0, trace.stderr + trace.stdout
    assert "PASS: autoresearch trace sampler" in trace.stdout

    lifecycle = run_script("scripts/check_lifecycle_datasets.py")
    assert lifecycle.returncode == 0, lifecycle.stderr + lifecycle.stdout
    assert "PASS: lifecycle datasets and openwiki display" in lifecycle.stdout


def test_wiki_graph_sync_gate_passes() -> None:
    with tempfile.TemporaryDirectory(prefix="wiki-graph-pytest-") as tmp:
        sync = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/sync_wiki_to_graph.py"),
                "--event-log",
                str(Path(tmp) / "event_log.jsonl"),
                "--graph-out",
                str(Path(tmp) / "sample_graph.json"),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert sync.returncode == 0, sync.stderr + sync.stdout
        assert "PASS: wiki graph sync" in sync.stdout

    gate = run_script("scripts/check_wiki_graph_sync.py")
    assert gate.returncode == 0, gate.stderr + gate.stdout
    assert "PASS: wiki graph sync architecture and artifacts" in gate.stdout


def test_molecular_commit_lineage_selftest_passes() -> None:
    # Asserts the selftest, not the ledger. The ledger describes commits in the authoring
    # workspace, which a checkout of this repository does not have; validating it from here
    # only ever exercised the no-argument schema path. See the note in scripts/git_gate.py.
    lineage = run_script("scripts/validate_molecular_commit_lineage.py", "--selftest")
    assert lineage.returncode == 0, lineage.stderr + lineage.stdout
    assert "SELFTEST GREEN" in lineage.stdout
