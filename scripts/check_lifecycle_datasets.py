#!/usr/bin/env python3
"""Validate structured lifecycle datasets and their OpenWiki display layer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(os.environ.get("LIFECYCLE_DATASETS_ROOT", Path(__file__).resolve().parents[1]))


def read_json(relative: str) -> object:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def read_jsonl(relative: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (ROOT / relative).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    failures: list[str] = []
    registry = read_json("data/lifecycle/skill_optimization_registry.json")
    versions = read_json("data/lifecycle/golden_dataset_versions.json")
    eval_run = read_json("data/lifecycle/eval_runs/autoresearch_composer_2026-07-23.json")
    promotions = read_json("data/lifecycle/promotion_records.json")
    privacy = read_json("data/lifecycle/trace_privacy_classification.json")
    drift = read_jsonl("data/lifecycle/dataset_drift_history.jsonl")
    failure_traces = read_jsonl("data/autoresearch_traces/failure_trace_samples.jsonl")
    openwiki = (ROOT / "openwiki" / "nonofficial" / "structured-lifecycle-data.md").read_text(encoding="utf-8")
    rendered = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "render_lifecycle_openwiki.py"), "--stdout"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if rendered.returncode != 0:
        failures.append("lifecycle openwiki renderer failed")
    elif openwiki != rendered.stdout:
        failures.append("structured lifecycle openwiki must equal renderer output")

    skills = registry.get("skills", []) if isinstance(registry, dict) else []
    if len(skills) != 1 or skills[0].get("skill_id") != "autoresearch_composer":
        failures.append("registry must contain autoresearch_composer as a managed skill")
    if skills and skills[0].get("current_status") != "production-seed-candidate":
        failures.append("autoresearch_composer status must be production-seed-candidate")

    datasets = versions.get("datasets", []) if isinstance(versions, dict) else []
    expected_versions = {
        "autoresearch-pr-golden@2026-07-23": 4,
        "autoresearch-nightly-golden@2026-07-23": 3,
    }
    observed_versions = {item.get("dataset_version"): item.get("case_count") for item in datasets}
    for dataset_version, case_count in expected_versions.items():
        if observed_versions.get(dataset_version) != case_count:
            failures.append(f"dataset version mismatch: {dataset_version}")

    summary = eval_run.get("result_summary", {}) if isinstance(eval_run, dict) else {}
    expected_summary = {
        "pr_cases": 4,
        "pr_passed": 4,
        "nightly_cases": 3,
        "nightly_passed": 3,
        "trace_samples": 3,
        "failure_trace_samples": 2,
        "ablation_delta": 1.0,
        "status": "PASS",
    }
    for key, value in expected_summary.items():
        if summary.get(key) != value:
            failures.append(f"eval run summary mismatch: {key}")

    records = promotions.get("records", []) if isinstance(promotions, dict) else []
    if len(records) != 1 or records[0].get("promotion_status") != "candidate_until_human_admit":
        failures.append("promotion record must remain candidate_until_human_admit")
    if records and records[0].get("human_admit") != "required_before_promotion":
        failures.append("promotion record must require human admit")
    if records:
        record = records[0]
        binding = record.get("human_admit_binding", {})
        required_inputs = [
            "gcr-047d548-conversation",
            "real-synthetic-case-generation-research",
            "gcr-37731ad-eval-guardrails-lifecycle",
            "gcr-874b5c-event-sourcing-graphrag",
        ]
        if not isinstance(binding, dict):
            failures.append("promotion record must contain structured human_admit_binding")
            binding = {}
        if binding.get("binding_status") != "prepared_unsigned":
            failures.append("human_admit_binding must be prepared_unsigned before human admit")
        for key in ("dataset_versions", "molecular_commit", "plan_package_input_ids", "route", "routes_edge_id", "lineage_edge_id"):
            if key not in binding:
                failures.append(f"human_admit_binding missing key: {key}")
        if binding.get("dataset_versions") != record.get("dataset_versions"):
            failures.append("human_admit_binding dataset_versions must match promotion record")
        if binding.get("molecular_commit") != record.get("molecular_commit"):
            failures.append("human_admit_binding molecular_commit must match promotion record")
        if binding.get("plan_package_input_ids") != required_inputs:
            failures.append("human_admit_binding must bind all plan-package input ids")
        if binding.get("route") != "ROUTES.md#plan-package-materialization":
            failures.append("human_admit_binding must bind ROUTES plan-package-materialization route")
        if binding.get("routes_edge_id") != "plan-package-materialization":
            failures.append("human_admit_binding must bind ROUTES edge id")
        if binding.get("lineage_edge_id") != "EDGE-066":
            failures.append("human_admit_binding must bind final lineage edge id")

    if privacy.get("cloud_upload_allowed") is not False:
        failures.append("trace privacy root cloud_upload_allowed must be false")
    for dataset in privacy.get("datasets", []):
        if dataset.get("cloud_allowed") is not False:
            failures.append(f"trace dataset cloud_allowed must be false: {dataset.get('path')}")

    if len(drift) != 2:
        failures.append("drift history must contain 2 seed rows")
    for row in drift:
        if row.get("pass_rate") != 1.0 or row.get("failure_trace_sample_count") != 2:
            failures.append(f"drift row mismatch: {row.get('dataset_version')}")
        route_distribution = row.get("route_distribution")
        if not isinstance(route_distribution, dict) or not route_distribution:
            failures.append(f"drift row must contain route_distribution: {row.get('dataset_version')}")
        elif sum(int(value) for value in route_distribution.values()) != row.get("case_count"):
            failures.append(f"drift route_distribution must sum to case_count: {row.get('dataset_version')}")

    if len(failure_traces) != 2:
        failures.append("failure trace dataset must contain exactly 2 seed rows")
    for trace in failure_traces:
        if trace.get("verdict") != "FAIL" or trace.get("cloud_judge_enabled") is not False:
            failures.append(f"failure trace must be FAIL and local-only: {trace.get('trace_id')}")

    required_literals = [
        "Structured Lifecycle Data",
        "data/lifecycle/skill_optimization_registry.json",
        "scripts/render_lifecycle_openwiki.py",
        "autoresearch_composer",
        "autoresearch-pr-golden@2026-07-23",
        "autoresearch-nightly-golden@2026-07-23",
        "autoresearch-composer-2026-07-23-local",
        "4/4",
        "3/3",
        "delta=1.0",
        "synthetic-local-failure",
        "candidate_until_human_admit",
        "plan-package-materialization",
        "EDGE-066",
        "failure_trace_sample_count",
        "route_distribution",
    ]
    for literal in required_literals:
        if literal not in openwiki:
            failures.append(f"structured lifecycle openwiki missing literal: {literal}")

    if failures:
        print("FAIL: lifecycle dataset validation failed", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 2
    print("PASS: lifecycle datasets and openwiki display")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
