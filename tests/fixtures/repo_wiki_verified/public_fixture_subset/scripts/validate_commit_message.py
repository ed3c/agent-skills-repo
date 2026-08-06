#!/usr/bin/env python3
"""Validate molecular commit messages for plan-package traceability."""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path


REQUIRED_FIELDS = (
    "Intent-Slice:",
    "Route:",
    "Plan-Package:",
    "Small-Loop:",
    "Final-Repo:",
    "Exchange-Format:",
    "Exchange-Packet:",
    "Fixed-Prompt-Context:",
    "Iteration-Auto-Context:",
    "Emergent-Prompt-Context:",
    "Dataflow:",
)


def workspace_root() -> Path:
    path = Path(__file__).resolve()
    if len(path.parents) >= 4 and path.parents[1].as_posix().endswith("repo/agent-skills-repo"):
        return path.parents[3]
    for parent in path.parents:
        if (parent / "loop_wiki" / "evolve-unknown-discovery-plan-truth").exists():
            return parent
    return Path("<host-repo>")


def field_value(text: str, field: str) -> str | None:
    match = re.search(rf"^{re.escape(field)}\s*(.+)$", text, flags=re.MULTILINE)
    return match.group(1).strip() if match else None


def validate_text(text: str) -> list[str]:
    failures: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in text:
            failures.append(f"missing field: {field}")
    root = workspace_root()
    if field_value(text, "Plan-Package:") != f"{root}/loop_wiki/evolve-unknown-discovery-plan-truth/plan-package.yaml":
        failures.append("Plan-Package must reference the canonical GCR plan package")
    if field_value(text, "Small-Loop:") != f"{root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/":
        failures.append("Small-Loop must reference the canonical materialized small loop")
    if field_value(text, "Final-Repo:") != f"{root}/repo/agent-skills-repo/":
        failures.append("Final-Repo must reference the canonical agent-skills repo")
    if not re.search(r"Intent-Slice:\s+GCR-SLICE-\d{2}", text):
        failures.append("Intent-Slice must be GCR-SLICE-XX")
    absolute_refs = re.findall(rf"{re.escape(str(root))}/\S+", text)
    if len(absolute_refs) < 5:
        failures.append("commit message must include at least five absolute workspace dataflow paths")
    if "<home>/antigravity/gemini_research/gcr/047d548af8f8e34c-conversation.md" not in text:
        failures.append("commit message must reference the original GCR conversation source")
    if "ROUTES.md#plan-package-materialization" not in text:
        failures.append("commit message must reference the materialization route")
    if "modules/exchange-formats.md" not in text:
        failures.append("commit message must reference the exchange format SSOT")
    return failures


def validate_file(path: Path) -> int:
    if not path.is_file():
        print(f"FAIL: commit message file does not exist: {path}", file=sys.stderr)
        return 2
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        print(f"FAIL: invalid UTF-8 commit message file: {path}", file=sys.stderr)
        return 2
    failures = validate_text(text)
    if failures:
        print("FAIL: commit message traceability contract failed", file=sys.stderr)
        print("\n".join(failures), file=sys.stderr)
        return 2
    print("PASS: commit message traceability contract")
    return 0


def selftest() -> int:
    root = workspace_root()
    good = f"""Implement endpoint lineage hardening

Intent-Slice: GCR-SLICE-06
Route: {root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/ROUTES.md#plan-package-materialization
Plan-Package: {root}/loop_wiki/evolve-unknown-discovery-plan-truth/plan-package.yaml
Small-Loop: {root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/
Final-Repo: {root}/repo/agent-skills-repo/
Exchange-Format: {root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/modules/exchange-formats.md
Exchange-Packet: {root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/packets/outbox/plan-package-materialization-agent-skills-repo.yaml
Fixed-Prompt-Context:
- {root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/PROMPT.md
- {root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/modules/semantic-truth-context.md
- {root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/modules/development-standards.md
Iteration-Auto-Context: docs/plans/2026-07-22-unknown-discovery-gcr-order/invariants/agent-skills-repo/2026-07-23-draft-quality-feedback.md
Emergent-Prompt-Context: loop_wiki/evolve-unknown-discovery-plan-truth/data/agy-replay-execution-report.json
Dataflow:
<home>/antigravity/gemini_research/gcr/047d548af8f8e34c-conversation.md
  -> {root}/loop_wiki/evolve-unknown-discovery-plan-truth/inputs/plan-package-inputs.yaml
  -> {root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/ROUTES.md#plan-package-materialization
  -> {root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/modules/exchange-formats.md
  -> {root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/plan-package.yaml
"""
    bad = f"""Implement endpoint lineage hardening

Intent-Slice: GCR-SLICE-06
Route: {root}/prototype/unknown-discovery-gcr-order/agent-skills-repo/small-loop/ROUTES.md#plan-package-materialization
"""
    if validate_text(good):
        print("FAIL: good commit message did not validate", file=sys.stderr)
        return 2
    if not validate_text(bad):
        print("FAIL: hollow commit message unexpectedly validated", file=sys.stderr)
        return 2
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
        handle.write(good)
        temp_path = Path(handle.name)
    try:
        return validate_file(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    if not argv or argv == ["--selftest"]:
        return selftest()
    if len(argv) == 1:
        return validate_file(Path(argv[0]))
    print(f"FAIL: unknown arguments: {' '.join(argv)}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
