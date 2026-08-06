#!/usr/bin/env python3
# Local-first Wiki -> Event Log -> Graph projection.

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(os.environ.get("SYNC_WIKI_ROOT", Path(__file__).resolve().parents[1]))
GENERATED_EVENT_TIMESTAMP = "2026-07-23T00:00:00Z"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_schema(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"wiki graph schema file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid wiki graph schema JSON: {path}") from exc


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    meta: dict[str, str] = {}
    for line in raw.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            meta[key.strip()] = value.strip().strip('"')
    return meta, text[end + 5 :]


def extract_headings(body: str) -> list[dict[str, object]]:
    headings: list[dict[str, object]] = []
    stack: list[str] = []
    for line in body.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        level = len(match.group(1))
        title = match.group(2).strip()
        stack = stack[: level - 1] + [title]
        headings.append({"level": level, "title": title, "heading_path": list(stack)})
    return headings


def extract_links(body: str) -> list[str]:
    return sorted(set(re.findall(r"\[[^\]]+\]\(([^)]+)\)", body)))


def code_block_count(body: str) -> int:
    return body.count("```") // 2


def license_payload(meta: dict[str, str]) -> dict[str, str]:
    return {
        "code_license": meta.get("code_license", "MIT"),
        "model_license": meta.get("model_license", "N/A-deterministic-local-hash-no-model-weights"),
        "data_license": meta.get("data_license", "local-project-docs"),
        "commercial_use": meta.get("commercial_use", "allowed-local-artifact"),
        "copyleft_risk": meta.get("copyleft_risk", "none"),
    }


def event_for_file(path: Path, wiki_root: Path, commit_sha: str, schema: dict[str, object]) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid UTF-8 wiki markdown: {path}") from exc
    meta, body = parse_frontmatter(text)
    relative = str(path.relative_to(ROOT))
    headings = extract_headings(body)
    title = meta.get("title") or (headings[0]["title"] if headings else path.stem)
    content_hash = sha256_text(text)
    event_id = "evt:" + sha256_text(f"{relative}:{commit_sha}:{content_hash}")[:24]
    return {
        "event_id": event_id,
        "event_type": "wiki_page_indexed",
        "schema_version": schema["event_schema_version"],
        "source_path": relative,
        "heading_path": [str(title)],
        "commit_sha": commit_sha,
        "content_sha256": content_hash,
        "occurred_at": GENERATED_EVENT_TIMESTAMP,
        "payload": {
            "title": str(title),
            "headings": headings,
            "links": extract_links(body),
            "code_block_count": code_block_count(body),
            "license": license_payload(meta),
        },
    }


def project_graph(events: list[dict[str, object]], schema: dict[str, object]) -> dict[str, object]:
    nodes: list[dict[str, object]] = []
    edges: list[dict[str, object]] = []
    chunks: list[dict[str, object]] = []
    embedding = schema["default_embedding_model"]
    for event in events:
        payload = event["payload"]
        source_path = str(event["source_path"])
        doc_id = "doc:" + source_path
        nodes.append({
            "node_id": doc_id,
            "node_type": "WikiDocument",
            "title": payload["title"],
            "source_path": source_path,
            "commit_sha": event["commit_sha"],
            "content_sha256": event["content_sha256"],
            "schema_version": schema["schema_version"],
            "license": payload["license"],
        })
        for index, heading in enumerate(payload.get("headings", [])):
            section_id = f"section:{source_path}:{index}"
            nodes.append({
                "node_id": section_id,
                "node_type": "WikiSection",
                "source_path": source_path,
                "heading_path": heading["heading_path"],
                "level": heading["level"],
                "content_sha256": event["content_sha256"],
            })
            edges.append({"src": doc_id, "dst": section_id, "edge_type": "HAS_SECTION", "source_event_id": event["event_id"], "commit_sha": event["commit_sha"]})
            chunks.append({
                "chunk_id": f"chunk:{source_path}:{index}",
                "source_path": source_path,
                "heading_path": heading["heading_path"],
                "text": " > ".join(heading["heading_path"]),
                "content_sha256": event["content_sha256"],
                "embedding_model_id": embedding["embedding_model_id"],
            })
        for link in payload.get("links", []):
            edges.append({"src": doc_id, "dst": str(link), "edge_type": "LINKS_TO", "source_event_id": event["event_id"], "commit_sha": event["commit_sha"]})
        edges.append({"src": doc_id, "dst": str(schema["schema_version"]), "edge_type": "USES_SCHEMA", "source_event_id": event["event_id"], "commit_sha": event["commit_sha"]})
    return {
        "schema_version": schema["schema_version"],
        "nodes": nodes,
        "edges": edges,
        "chunks": chunks,
        "retrieval": {
            "vector_rag": {"enabled": True, "vector_store": "local-json", "embedding_model_id": embedding["embedding_model_id"]},
            "graphrag": {"enabled": True, "graph_store": "local-json", "external_write_enabled": False},
            "fusion": {"required_fields": ["source_path", "heading_path", "commit_sha", "schema_version"]},
        },
    }


def write_external_graph_if_enabled(graph: dict[str, object]) -> None:
    if os.environ.get("ENABLE_GRAPH_DB_WRITE") != "true":
        return
    required = ("GRAPH_DB_URI", "GRAPH_DB_USER", "GRAPH_DB_PASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("external graph write requested but missing secrets: " + ", ".join(missing))
    graph_kind = os.environ.get("GRAPH_DB_KIND", "generic-http-json")
    if graph_kind == "generic-http-json":
        post_generic_graph_payload(graph)
    elif graph_kind == "neo4j-http":
        post_neo4j_graph_payload(graph)
    else:
        raise RuntimeError(f"unsupported GRAPH_DB_KIND: {graph_kind}")


def graph_auth_header() -> str:
    user = os.environ["GRAPH_DB_USER"]
    password = os.environ["GRAPH_DB_PASSWORD"]
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return "Basic " + token


def post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": graph_auth_header(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
            return json.loads(response_body) if response_body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"external graph write failed HTTP {exc.code}: {detail}") from exc


def post_generic_graph_payload(graph: dict[str, object]) -> None:
    response = post_json(os.environ["GRAPH_DB_URI"], {"graph": graph})
    print("external_graph_write=generic-http-json response_keys=" + ",".join(sorted(response.keys())))


def post_neo4j_graph_payload(graph: dict[str, object]) -> None:
    base_uri = os.environ["GRAPH_DB_URI"].rstrip("/")
    database = os.environ.get("GRAPH_DB_DATABASE", "neo4j")
    endpoint = f"{base_uri}/db/{database}/tx/commit"
    payload = {
        "statements": [
            {
                "statement": "UNWIND $nodes AS row MERGE (n:WikiNode {node_id: row.node_id}) SET n += row",
                "parameters": {"nodes": graph["nodes"]},
            },
            {
                "statement": "UNWIND $chunks AS row MERGE (c:WikiChunk {chunk_id: row.chunk_id}) SET c += row",
                "parameters": {"chunks": graph["chunks"]},
            },
            {
                "statement": (
                    "UNWIND $edges AS row "
                    "MERGE (a:WikiNode {node_id: row.src}) "
                    "MERGE (b:WikiNode {node_id: row.dst}) "
                    "MERGE (a)-[r:WIKI_EDGE {edge_type: row.edge_type, source_event_id: row.source_event_id}]->(b) "
                    "SET r += row"
                ),
                "parameters": {"edges": graph["edges"]},
            },
        ]
    }
    response = post_json(endpoint, payload)
    errors = response.get("errors", [])
    if errors:
        raise RuntimeError("neo4j graph write returned errors: " + json.dumps(errors, ensure_ascii=False))
    print("external_graph_write=neo4j-http statements=3")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wiki-root", type=Path, default=ROOT / "openwiki")
    parser.add_argument("--schema", type=Path, default=ROOT / "data/wiki_graph/schema.json")
    parser.add_argument("--event-log", type=Path, default=ROOT / "data/wiki_graph/event_log.jsonl")
    parser.add_argument("--graph-out", type=Path, default=ROOT / "data/wiki_graph/sample_graph.json")
    parser.add_argument("--commit-sha", default="local-dev")
    parser.add_argument("--write-external-graph", action="store_true")
    args = parser.parse_args()

    schema = load_schema(args.schema)
    wiki_root = args.wiki_root if args.wiki_root.is_absolute() else ROOT / args.wiki_root
    md_files = sorted(path for path in wiki_root.rglob("*.md") if path.is_file())
    if not md_files:
        print("FAIL: no wiki markdown files found", file=sys.stderr)
        return 2
    events = [event_for_file(path, wiki_root, args.commit_sha, schema) for path in md_files]
    graph = project_graph(events, schema)

    args.event_log.parent.mkdir(parents=True, exist_ok=True)
    args.graph_out.parent.mkdir(parents=True, exist_ok=True)
    args.event_log.write_text("\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in events) + "\n", encoding="utf-8")
    args.graph_out.write_text(json.dumps(graph, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.write_external_graph:
        write_external_graph_if_enabled(graph)
    print(f"PASS: wiki graph sync events={len(events)} nodes={len(graph['nodes'])} edges={len(graph['edges'])} chunks={len(graph['chunks'])}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
