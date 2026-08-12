#!/usr/bin/env python3
"""Verify one exact, preregistered quote repair with no network access."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    task = json.loads(Path("/app/task.json").read_text(encoding="utf-8"))
    planted_anchor = (
        f"(src: {task['source_path']} `{task['planted_quote']}`)"
    )
    expected_anchor = (
        f"(src: {task['source_path']} `{task['expected_repair_quote']}`)"
    )
    diagnostic_path = Path("/app/adapter-diagnostic.json")
    if diagnostic_path.exists():
        try:
            diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            diagnostic = None
        expected_fields = {
            "schema_version", "task_id", "status", "repair_attempts", "detail"
        }
        if (
            not isinstance(diagnostic, dict)
            or set(diagnostic) != expected_fields
            or diagnostic.get("schema_version")
            != "quote-repair-adapter-diagnostic@1"
            or diagnostic.get("task_id") != task.get("task_id")
            or diagnostic.get("status") not in {"no_candidate", "search_incomplete"}
            or type(diagnostic.get("repair_attempts")) is not int
            or not 0 <= diagnostic["repair_attempts"] <= 1
            or not isinstance(diagnostic.get("detail"), str)
            or not diagnostic["detail"]
        ):
            status = "malformed_repair"
        else:
            status = diagnostic["status"]
        passed = False
    else:
        try:
            wiki = Path("/app/wiki.md").read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            status = "malformed_repair"
            passed = False
        else:
            template = task["wiki_template"]
            expected_wiki = template.replace(planted_anchor, expected_anchor)
            passed = template.count(planted_anchor) == 1 and wiki == expected_wiki
            status = "succeeded" if passed else "wrong_file_or_span"
    Path("/logs/verifier/reward.txt").write_text(
        "1" if passed else "0", encoding="utf-8"
    )
    Path("/logs/verifier/diagnostics.json").write_text(
        json.dumps(
            {
                "schema_version": "quote-repair-verifier@1",
                "status": status,
                "reward": 1 if passed else 0,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
