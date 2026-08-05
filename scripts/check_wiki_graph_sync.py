#!/usr/bin/env python3
# Validate Wiki -> Event Log -> GraphRAG/Vector RAG sync artifacts.

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(os.environ.get("WIKI_GRAPH_ROOT", Path(__file__).resolve().parents[1]))


def read_json(path: str) -> object:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def read_jsonl(path: str) -> list[dict[str, object]]:
    rows = []
    for line in (ROOT / path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main() -> int:
    failures: list[str] = []
    required_files = [
        ".github/workflows/wiki_graph_sync.yml",
        "openwiki/nonofficial/wiki-graph-sync-architecture.md",
        "openwiki/nonofficial/schema-standards.md",
        "scripts/sync_wiki_to_graph.py",
        "data/wiki_graph/schema.json",
        "data/wiki_graph/event_log.jsonl",
        "data/wiki_graph/sample_graph.json",
    ]
    for relative in required_files:
        if not (ROOT / relative).is_file():
            failures.append(f"missing file: {relative}")
    if failures:
        print("FAIL: wiki graph sync validation failed", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 2

    workflow = (ROOT / ".github/workflows/wiki_graph_sync.yml").read_text(encoding="utf-8")
    for literal in (
        "openwiki/**/*.md",
        "python scripts/sync_wiki_to_graph.py",
        "python scripts/check_wiki_graph_sync.py",
        "ENABLE_GRAPH_DB_WRITE",
        "GRAPH_DB_URI",
        "wiki-graph-local-projection",
    ):
        if literal not in workflow:
            failures.append(f"workflow missing literal: {literal}")

    architecture = (ROOT / "openwiki/nonofficial/wiki-graph-sync-architecture.md").read_text(encoding="utf-8")
    for literal in ("Event Sourcing", "Vector RAG", "GraphRAG", "LLM Wiki", "W3.license_unknown", "track | acceptable default", "GRAPH_DB_KIND=generic-http-json", "GRAPH_DB_KIND=neo4j-http"):
        if literal not in architecture:
            failures.append(f"architecture page missing literal: {literal}")

    standards = (ROOT / "openwiki/nonofficial/schema-standards.md").read_text(encoding="utf-8")
    for literal in ("WikiDocument", "WikiEvent", "EmbeddingMetadata", "LicenseProvenance", "Apache-2.0"):
        if literal not in standards:
            failures.append(f"schema standards missing literal: {literal}")

    with tempfile.TemporaryDirectory(prefix="wiki-graph-check-") as tmp:
        temp_event_log = Path(tmp) / "event_log.jsonl"
        temp_graph = Path(tmp) / "sample_graph.json"
        sync = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/sync_wiki_to_graph.py"),
                "--commit-sha",
                "check-local",
                "--event-log",
                str(temp_event_log),
                "--graph-out",
                str(temp_graph),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if sync.returncode != 0 or "PASS: wiki graph sync" not in sync.stdout:
            failures.append("sync_wiki_to_graph.py did not pass: " + sync.stdout + sync.stderr)

    sync_script = (ROOT / "scripts/sync_wiki_to_graph.py").read_text(encoding="utf-8")
    for literal in ("urllib.request", "GRAPH_DB_KIND", "generic-http-json", "neo4j-http", "post_neo4j_graph_payload"):
        if literal not in sync_script:
            failures.append(f"sync script missing external graph writer literal: {literal}")

    missing_secret_env = os.environ.copy()
    missing_secret_env["ENABLE_GRAPH_DB_WRITE"] = "true"
    for name in ("GRAPH_DB_URI", "GRAPH_DB_USER", "GRAPH_DB_PASSWORD"):
        missing_secret_env.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="wiki-graph-missing-secret-") as tmp:
        temp_event_log = Path(tmp) / "event_log.jsonl"
        temp_graph = Path(tmp) / "sample_graph.json"
        missing_secret = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/sync_wiki_to_graph.py"),
                "--write-external-graph",
                "--commit-sha",
                "missing-secret-check",
                "--event-log",
                str(temp_event_log),
                "--graph-out",
                str(temp_graph),
            ],
            cwd=ROOT,
            env=missing_secret_env,
            text=True,
            capture_output=True,
            check=False,
        )
        if missing_secret.returncode == 0 or "missing secrets" not in (missing_secret.stdout + missing_secret.stderr):
            failures.append("external graph write must fail fast when enabled without secrets")

    schema = read_json("data/wiki_graph/schema.json")
    events = read_jsonl("data/wiki_graph/event_log.jsonl")
    graph = read_json("data/wiki_graph/sample_graph.json")
    if schema.get("schema_version") != "wiki-graph-schema@0.1.0":
        failures.append("schema_version mismatch")
    if not events:
        failures.append("event log must contain at least one event")
    for event in events:
        for field in schema.get("required_event_fields", []):
            if field not in event:
                failures.append(f"event missing field: {field}")
        license_payload = event.get("payload", {}).get("license", {})
        for field in schema.get("required_provenance_fields", []):
            if field not in license_payload:
                failures.append(f"event license missing field: {field}")
    for field in schema.get("required_graph_fields", []):
        if field not in graph:
            failures.append(f"graph missing field: {field}")
    retrieval = graph.get("retrieval", {})
    if not retrieval.get("vector_rag", {}).get("enabled"):
        failures.append("Vector RAG contract must be enabled")
    if not retrieval.get("graphrag", {}).get("enabled"):
        failures.append("GraphRAG contract must be enabled")
    if retrieval.get("graphrag", {}).get("external_write_enabled") is not False:
        failures.append("external graph write must be disabled by default")
    if not graph.get("chunks"):
        failures.append("graph must include chunks for Vector RAG")
    if not graph.get("edges"):
        failures.append("graph must include edges for GraphRAG")

    if failures:
        print("FAIL: wiki graph sync validation failed", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 2
    print("PASS: wiki graph sync architecture and artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
