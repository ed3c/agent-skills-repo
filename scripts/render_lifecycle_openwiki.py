#!/usr/bin/env python3
"""Render OpenWiki lifecycle Markdown from structured lifecycle datasets."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read_json(relative: str) -> object:
    path = ROOT / relative
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid lifecycle JSON: {path}") from exc


def read_jsonl(relative: str) -> list[dict[str, object]]:
    path = ROOT / relative
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception as exc:
        raise ValueError(f"invalid lifecycle JSONL: {path}") from exc


def bool_text(value: object) -> str:
    return "true" if value is True else "false" if value is False else str(value)


def render() -> str:
    registry = read_json("data/lifecycle/skill_optimization_registry.json")
    versions = read_json("data/lifecycle/golden_dataset_versions.json")
    eval_run = read_json("data/lifecycle/eval_runs/autoresearch_composer_2026-07-23.json")
    promotions = read_json("data/lifecycle/promotion_records.json")
    privacy = read_json("data/lifecycle/trace_privacy_classification.json")
    drift = read_jsonl("data/lifecycle/dataset_drift_history.jsonl")

    skills = registry.get("skills", []) if isinstance(registry, dict) else []
    datasets = versions.get("datasets", []) if isinstance(versions, dict) else []
    records = promotions.get("records", []) if isinstance(promotions, dict) else []
    privacy_datasets = privacy.get("datasets", []) if isinstance(privacy, dict) else []
    summary = eval_run.get("result_summary", {}) if isinstance(eval_run, dict) else {}

    lines = [
        "# Structured Lifecycle Data",
        "",
        "This page is the Markdown display layer for machine-readable lifecycle data.",
        "The structured SSOT lives under `data/lifecycle/`; this page must not invent",
        "numbers that are absent from those files.",
        "",
        "## Dataflow",
        "",
        "```text",
        "data/lifecycle/skill_optimization_registry.json",
        "  -> data/lifecycle/golden_dataset_versions.json",
        "  -> data/lifecycle/eval_runs/autoresearch_composer_2026-07-23.json",
        "  -> data/lifecycle/promotion_records.json",
        "  -> data/lifecycle/dataset_drift_history.jsonl",
        "  -> data/lifecycle/trace_privacy_classification.json",
        "  -> scripts/render_lifecycle_openwiki.py",
        "  -> openwiki/nonofficial/structured-lifecycle-data.md",
        "  -> scripts/check_lifecycle_datasets.py",
        "```",
        "",
        "## Multi-Skill Registry",
        "",
        "| skill_id | status | openwiki | eval run | cloud policy |",
        "|---|---|---|---|---|",
    ]
    for skill in skills:
        eval_run_ids = skill.get('eval_run_ids', [])
        latest_eval_run_id = eval_run_ids[-1] if isinstance(eval_run_ids, list) and eval_run_ids else ""
        lines.append(
            f"| `{skill.get('skill_id')}` | `{skill.get('current_status')}` | "
            f"`{skill.get('openwiki_page')}` | `{latest_eval_run_id}` | "
            f"`{skill.get('cloud_policy')}` |"
        )

    lines.extend(
        [
            "",
            "## Golden Dataset Versions",
            "",
            "| dataset_version | path | cases | use |",
            "|---|---|---:|---|",
        ]
    )
    for dataset in datasets:
        lines.append(
            f"| `{dataset.get('dataset_version')}` | `{dataset.get('path')}` | "
            f"{dataset.get('case_count')} | {dataset.get('promotion_use')} |"
        )

    lines.extend(
        [
            "",
            "## Dated Eval Run",
            "",
            "| run_id | PR | nightly | trace | failure trace | ablation |",
            "|---|---:|---:|---:|---:|---|",
            (
                f"| `{eval_run.get('run_id')}` | `{summary.get('pr_passed')}/{summary.get('pr_cases')}` | "
                f"`{summary.get('nightly_passed')}/{summary.get('nightly_cases')}` | "
                f"{summary.get('trace_samples')} | {summary.get('failure_trace_samples')} | "
                f"`delta={summary.get('ablation_delta')}`, `verdict={summary.get('status')}` |"
            ),
            "",
            "## Trace Privacy",
            "",
            "| dataset | classification | cloud allowed |",
            "|---|---|---|",
        ]
    )
    for dataset in privacy_datasets:
        lines.append(
            f"| `{dataset.get('path')}` | `{dataset.get('classification')}` | "
            f"{bool_text(dataset.get('cloud_allowed'))} |"
        )

    lines.extend(
        [
            "",
            "## Promotion Record",
            "",
            "| record_id | molecular commit | route edge | lineage edge | status | human gate |",
            "|---|---|---|---|---|---|",
        ]
    )
    for record in records:
        lines.append(
            f"| `{record.get('record_id')}` | `{record.get('molecular_commit')}` | "
            f"`{record.get('routes_edge_id')}` | `{record.get('lineage_edge_id')}` | "
            f"`{record.get('promotion_status')}` | `{record.get('human_admit')}` |"
        )

    lines.extend(
        [
            "",
            "## Drift Metrics",
            "",
            "| dataset_version | case_count | pass_rate | judge_score_avg | trace_sample_count | failure_trace_sample_count | route_distribution |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in drift:
        route_distribution = row.get("route_distribution", {})
        route_text = ", ".join(f"{key}={value}" for key, value in sorted(route_distribution.items()))
        lines.append(
            f"| `{row.get('dataset_version')}` | {row.get('case_count')} | {row.get('pass_rate')} | "
            f"{row.get('judge_score_avg')} | {row.get('trace_sample_count')} | "
            f"{row.get('failure_trace_sample_count')} | `{route_text}` |"
        )

    lines.extend(
        [
            "",
            "## Extension Rule For More Skills",
            "",
            "For every new optimized skill, add exactly one registry row, at least one Golden",
            "Dataset version, one dated eval run, one promotion record, trace privacy entries,",
            "and one drift-history row. Then expose the same IDs in openwiki and make",
            "`scripts/render_lifecycle_openwiki.py --write` and",
            "`scripts/check_lifecycle_datasets.py` pass.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    global ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    ROOT = args.repo_root
    try:
        output = render()
    except Exception as exc:
        print(f"FAIL: lifecycle openwiki renderer failed: {exc}", file=sys.stderr)
        return 2
    target = ROOT / "openwiki" / "nonofficial" / "structured-lifecycle-data.md"
    if args.write:
        target.write_text(output, encoding="utf-8")
        print("PASS: rendered lifecycle openwiki")
    elif args.stdout:
        print(output, end="")
    else:
        observed = target.read_text(encoding="utf-8")
        if observed != output:
            print("FAIL: lifecycle openwiki is not generated from structured data")
            return 2
        print("PASS: lifecycle openwiki renderer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
