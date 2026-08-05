#!/usr/bin/env python3
"""Validate local-first autoresearch trace samples."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TRACE_FIELDS = {
    "trace_id",
    "case_id",
    "route",
    "states",
    "verdict",
    "sample_reason",
    "cloud_judge_enabled",
}


def load_traces(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        raise ValueError(f"trace file does not exist: {path}")
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"invalid UTF-8 trace file: {path}") from exc
    traces: list[dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            trace = json.loads(line)
        except Exception as exc:
            raise ValueError(f"invalid trace JSONL: {path}:{line_number}") from exc
        if not isinstance(trace, dict):
            raise ValueError(f"trace line must be an object: {path}:{line_number}")
        traces.append(trace)
    return traces


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trace-file", type=Path, default=ROOT / "data" / "autoresearch_traces" / "local_trace_samples.jsonl")
    parser.add_argument("--min-samples", type=int, default=3)
    args = parser.parse_args()
    try:
        traces = load_traces(args.trace_file)
        failures: list[str] = []
        if len(traces) < args.min_samples:
            failures.append(f"trace sample count below minimum: {len(traces)} < {args.min_samples}")
        observed_states: set[str] = set()
        for trace in traces:
            missing = sorted(REQUIRED_TRACE_FIELDS - set(trace))
            if missing:
                failures.append(f"{trace.get('trace_id', '<unknown>')}: missing fields {missing}")
            states = trace.get("states", [])
            if not isinstance(states, list):
                failures.append(f"{trace.get('trace_id', '<unknown>')}: states must be list")
                states = []
            observed_states.update(str(state) for state in states)
            if trace.get("cloud_judge_enabled") is not False:
                failures.append(f"{trace.get('trace_id', '<unknown>')}: cloud_judge_enabled must be false in seed traces")
            if trace.get("verdict") not in {"PASS", "FAIL", "SKIP"}:
                failures.append(f"{trace.get('trace_id', '<unknown>')}: invalid verdict")
        for state in ("S1 match", "S2 route", "S3 generate", "S4 validate"):
            if state not in observed_states:
                failures.append(f"missing observed state: {state}")
    except Exception as exc:
        print(f"FAIL: autoresearch trace sampler error: {exc}", file=sys.stderr)
        return 2
    if failures:
        print("FAIL: autoresearch trace sampler " + "; ".join(failures), file=sys.stderr)
        return 2
    print(
        "PASS: autoresearch trace sampler "
        f"sample_count={len(traces)} observed_state_count={len(observed_states)} cloud_judge_enabled=false"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
