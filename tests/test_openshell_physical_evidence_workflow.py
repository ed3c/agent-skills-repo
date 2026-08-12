from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/openshell-physical-evidence.yml"


def _physical_job_sections() -> tuple[str, str]:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^  physical-pair:\n(?P<job>.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        workflow,
    )
    assert match is not None
    job = match.group("job")
    env_match = re.search(r"(?ms)^    env:\n(?P<env>.*?)(?=^    steps:\n)", job)
    assert env_match is not None
    return workflow, env_match.group("env")


def test_runner_context_is_not_used_before_a_runner_exists() -> None:
    workflow, job_env = _physical_job_sections()

    assert "${{ runner." not in job_env
    initializer = """      - name: Initialize evidence root
        run: echo \"EVIDENCE_ROOT=$RUNNER_TEMP/openshell-physical-evidence\" >> \"$GITHUB_ENV\"
"""
    assert initializer in workflow
    assert workflow.index(initializer) < workflow.index(
        "      - name: Fetch every repository ref for the private-key history audit"
    )


def test_workflow_contract_test_is_itself_in_the_offline_gate() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    test_path = "tests/test_openshell_physical_evidence_workflow.py"

    assert workflow.count(f"      - '{test_path}'") == 2
    assert f"          {test_path}" in workflow
