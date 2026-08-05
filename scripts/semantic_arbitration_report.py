#!/usr/bin/env python3
"""Check semantic arbitration evidence without promoting unrun actors."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(os.environ.get("SEMANTIC_ARBITRATION_ROOT", Path(__file__).resolve().parents[1]))
CLAIMS = ROOT / "data" / "semantic_arbitration_claims.json"
AGY_EXPERIENCE = ROOT / "data" / "agy_execution_experience.json"
STRICT_TERMINAL_ARTIFACTS = (ROOT / "plan-package.compat.yaml").is_file()
REQUIRED_SKILLS = {"judge-loop-chooser", "external-verify", "repo-agent-native"}
REQUIRED_ADVERSARIES = {"codex", "agy"}
REQUIRED_AGY_LESSONS = {
    "AGY-001",
    "AGY-002",
    "AGY-003",
    "AGY-004",
    "AGY-005",
    "AGY-006",
    "AGY-007",
    "AGY-008",
    "AGY-009",
    "AGY-010",
}
REQUIRED_AGY_MODEL_ID = "gemini-3.6-flash-high"
REQUIRED_AGY_REASONING_EFFORT = "high"
REQUIRED_AGY_THINKING_MODE = "extended"
REQUIRED_AGY_SELECTION_STATUS = "verified-model-inventory-and-file-output-canary"
REQUIRED_AGY_CANARY_TEXT = "CANARY_OK"


def fail(message: str) -> int:
    print(f"FAIL: {message}", file=sys.stderr)
    return 2


def read_json(path: Path) -> object:
    if not path.is_file():
        raise ValueError(f"semantic arbitration file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"invalid semantic arbitration JSON: {path}") from exc


def load_claims() -> list[dict[str, Any]]:
    payload = read_json(CLAIMS)
    if not isinstance(payload, dict) or "claims" not in payload:
        raise ValueError("claim data must be an object with claims")
    claims = payload["claims"]
    if not isinstance(claims, list) or not claims:
        raise ValueError("claims must be a non-empty list")
    return claims


def load_agy_experience() -> dict[str, Any]:
    payload = read_json(AGY_EXPERIENCE)
    if not isinstance(payload, dict) or payload.get("schema_version") != "agy-execution-experience@0.1.0":
        raise ValueError("agy experience profile has invalid schema_version")
    return payload


def as_set(value: object) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


def validate_claim(claim: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    claim_id = str(claim.get("claim_id", "unknown"))
    missing_keys = [
        key
        for key in (
            "claim_id",
            "claim_text",
            "grounding",
            "promotion_status",
            "terminal_code_artifacts",
            "terminal_data_artifacts",
            "skill_cross_checks",
            "adversarial_reviews",
        )
        if key not in claim
    ]
    if missing_keys:
        errors.append(f"{claim_id}: missing keys {missing_keys}")
        return errors

    skill_names = {str(item.get("skill")) for item in claim.get("skill_cross_checks", []) if isinstance(item, dict)}
    missing_skills = sorted(REQUIRED_SKILLS - skill_names)
    if missing_skills:
        errors.append(f"{claim_id}: missing skill cross-checks {missing_skills}")

    adversaries = {str(item.get("actor")) for item in claim.get("adversarial_reviews", []) if isinstance(item, dict)}
    missing_adversaries = sorted(REQUIRED_ADVERSARIES - adversaries)
    if missing_adversaries:
        errors.append(f"{claim_id}: missing adversarial reviews {missing_adversaries}")

    for field in ("terminal_code_artifacts", "terminal_data_artifacts"):
        paths = as_set(claim.get(field))
        if not paths:
            errors.append(f"{claim_id}: {field} is empty")
        for raw in sorted(paths):
            if raw.startswith("N/A-"):
                continue
            path = ROOT / raw
            if STRICT_TERMINAL_ARTIFACTS and not path.is_file():
                errors.append(f"{claim_id}: missing {field} path {raw}")

    if claim.get("promotion_status") == "promoted":
        agy_reviews = [
            item
            for item in claim.get("adversarial_reviews", [])
            if isinstance(item, dict) and item.get("actor") == "agy"
        ]
        if not agy_reviews or agy_reviews[0].get("status") != "executed-findings-only":
            errors.append(f"{claim_id}: promoted claim lacks executed agy findings")
    return errors


def validate_agy_experience(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    profile = payload.get("model_profile")
    if not isinstance(profile, dict):
        errors.append("agy experience model_profile must be an object")
    else:
        selected = str(profile.get("selected_model_id", ""))
        observed = as_set(profile.get("observed_model_ids"))
        if selected != REQUIRED_AGY_MODEL_ID:
            errors.append(f"agy selected_model_id must be {REQUIRED_AGY_MODEL_ID}")
        if REQUIRED_AGY_MODEL_ID not in observed:
            errors.append(f"agy observed_model_ids must include {REQUIRED_AGY_MODEL_ID}")
        if selected and selected not in observed:
            errors.append(f"agy selected_model_id is not present in observed_model_ids: {selected}")
        if str(profile.get("reasoning_effort", "")) != REQUIRED_AGY_REASONING_EFFORT:
            errors.append(f"agy reasoning_effort must be {REQUIRED_AGY_REASONING_EFFORT}")
        if str(profile.get("thinking_mode", "")) != REQUIRED_AGY_THINKING_MODE:
            errors.append(f"agy thinking_mode must be {REQUIRED_AGY_THINKING_MODE}")
        if str(profile.get("model_inventory_command", "")) != "agy models":
            errors.append("agy model_profile must record model_inventory_command=agy models")
        if str(profile.get("selection_status", "")) != REQUIRED_AGY_SELECTION_STATUS:
            errors.append(f"agy model_profile selection_status must be {REQUIRED_AGY_SELECTION_STATUS}")

    canary = payload.get("execution_canary")
    if not isinstance(canary, dict):
        errors.append("agy experience execution_canary must be an object")
    else:
        if str(canary.get("command_model_id", "")) != REQUIRED_AGY_MODEL_ID:
            errors.append(f"agy execution_canary command_model_id must be {REQUIRED_AGY_MODEL_ID}")
        if str(canary.get("command_effort", "")) != REQUIRED_AGY_REASONING_EFFORT:
            errors.append(f"agy execution_canary command_effort must be {REQUIRED_AGY_REASONING_EFFORT}")
        if str(canary.get("stdout_observed", "")) != "CANARY_DONE":
            errors.append("agy execution_canary stdout_observed must be CANARY_DONE")
        if str(canary.get("artifact_strip_equals", "")) != REQUIRED_AGY_CANARY_TEXT:
            errors.append(f"agy execution_canary artifact_strip_equals must be {REQUIRED_AGY_CANARY_TEXT}")
        if str(canary.get("verification_status", "")) != "passed-strip-equals":
            errors.append("agy execution_canary verification_status must be passed-strip-equals")

    lessons = payload.get("lessons")
    if not isinstance(lessons, list):
        return ["agy experience lessons must be a list"]
    ids = {str(item.get("lesson_id")) for item in lessons if isinstance(item, dict)}
    missing = sorted(REQUIRED_AGY_LESSONS - ids)
    if missing:
        errors.append(f"agy experience missing lessons {missing}")
    source_repo = str(payload.get("source_repo", ""))
    if source_repo != "<home>/antigravity":
        errors.append("agy experience source_repo must remain <home>/antigravity")
    required_phrases = ("silent no-op", "exact model ids", "stdout", "findings-only", REQUIRED_AGY_MODEL_ID)
    combined = json.dumps(payload, ensure_ascii=False)
    for phrase in required_phrases:
        if phrase not in combined:
            errors.append(f"agy experience missing required phrase: {phrase}")
    for item in lessons:
        if not isinstance(item, dict):
            errors.append("agy lesson must be an object")
            continue
        ref = str(item.get("source_ref", ""))
        if not ref.startswith("<home>/antigravity/") or ":" not in ref:
            errors.append(f"{item.get('lesson_id', 'unknown')}: invalid source_ref {ref}")
        if not item.get("portable_enforcement"):
            errors.append(f"{item.get('lesson_id', 'unknown')}: missing portable_enforcement")
    return errors


def main() -> int:
    try:
        claims = load_claims()
        agy_experience = load_agy_experience()
    except Exception as exc:
        return fail(f"cannot load semantic arbitration data: {exc}")

    errors: list[str] = []
    for claim in claims:
        if not isinstance(claim, dict):
            errors.append("claim entry must be an object")
            continue
        errors.extend(validate_claim(claim))
    errors.extend(validate_agy_experience(agy_experience))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 2

    code_count = sum(len(as_set(claim["terminal_code_artifacts"])) for claim in claims)
    data_count = sum(len(as_set(claim["terminal_data_artifacts"])) for claim in claims)
    review_statuses = [
        str(item.get("status"))
        for claim in claims
        for item in claim.get("adversarial_reviews", [])
        if isinstance(item, dict)
    ]
    executed = sum(1 for status in review_statuses if status == "executed-findings-only")
    pending = sum(1 for status in review_statuses if status != "executed-findings-only")
    print(
        "PASS: semantic arbitration perceived "
        "semantic_arbitration_status=candidate_until_human_admit "
        f"claim_count={len(claims)} "
        f"agy_execution_lessons={len(agy_experience['lessons'])} "
        f"agy_model={agy_experience['model_profile']['selected_model_id']} "
        f"agy_reasoning={agy_experience['model_profile']['reasoning_effort']} "
        f"agy_thinking={agy_experience['model_profile']['thinking_mode']} "
        f"agy_canary={agy_experience['execution_canary']['verification_status']} "
        f"terminal_code_artifacts={code_count} "
        f"terminal_data_artifacts={data_count} "
        f"executed_adversarial_reviews={executed} "
        f"pending_adversarial_reviews={pending}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
