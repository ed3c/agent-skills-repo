from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from skill_arena.experiment.model import ExperimentError, sha256_json
from skill_arena.experiment.quote_repair import load_quote_repair_task_bundle


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / "data/arena/quote-repair-interior-elision-tasks.json"
PROTOCOL = ROOT / "data/arena/quote-repair-study-protocol.json"
PROVIDER_POLICY = ROOT / "data/arena/ollama-qwen3-4b-local.json"
ENVIRONMENT_RECEIPT = (
    ROOT / "data/verification_runs/quote_repair_environment_image_2026-08-12.json"
)
PLAN = ROOT / "data/verification_runs/quote_repair_preregistered_plan_2026-08-12.json"
PUBLIC_KEY = ROOT / "data/arena/quote-repair-plan-public-key.json"
V2_SCHEMA = ROOT / "contracts/arena-experiment-v2.schema.json"
CHECKER = ROOT / "scripts/check_quote_repair_preregistration.py"


def _checker_module():
    spec = importlib.util.spec_from_file_location("quote_repair_checker", CHECKER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_task_bundle_mechanically_proves_every_round_zero_eligibility() -> None:
    bundle = load_quote_repair_task_bundle(TASKS)

    assert bundle["bundle_digest"] == (
        "sha256:dc3bed0c6d2052f94dcf8f4bb71737fa5f75d4b7a1453defea0dde0c8567c269"
    )
    assert len(bundle["tasks"]) == 3
    assert {
        (task["expected_round0_status"], task["expected_diagnostic_status"])
        for task in bundle["tasks"]
    } == {("quote_not_found", "interior_elision")}


def test_task_bundle_rejects_a_round_zero_success_even_after_redigest() -> None:
    bundle = json.loads(TASKS.read_text(encoding="utf-8"))
    task = bundle["tasks"][0]
    task["planted_quote"] = task["source_text"].strip()

    with pytest.raises(ExperimentError, match="already resolves"):
        from skill_arena.experiment.quote_repair import (
            validate_quote_repair_task_bundle,
        )

        validate_quote_repair_task_bundle(bundle)


def test_landed_signed_plan_is_v2_and_complete_before_execution() -> None:
    envelope = json.loads(PLAN.read_text(encoding="utf-8"))
    schema = json.loads(V2_SCHEMA.read_text(encoding="utf-8"))

    assert list(Draft202012Validator(schema).iter_errors(envelope)) == []
    malformed_block = json.loads(json.dumps(envelope))
    malformed_block["payload"]["blocks"][0] = {}
    assert list(Draft202012Validator(schema).iter_errors(malformed_block))
    malformed_invocation = json.loads(json.dumps(envelope))
    malformed_invocation["payload"]["invocations"][0] = {}
    assert list(Draft202012Validator(schema).iter_errors(malformed_invocation))
    assert envelope["schema_version"] == "arena-experiment-plan-envelope@2"
    plan = envelope["payload"]
    assert plan["planned_invocation_count"] == 30
    assert len(plan["blocks"]) == 15
    assert len(plan["invocations"]) == 30
    assert {
        row["skill_artifact_digest"]
        for row in plan["invocations"]
        if row["arm"] == "baseline"
    } == {"sha256:8019007c5eb01a1a7c15adc6a2c6afbeae12ba0df3e16d0c370b58120dbb2abb"}
    assert {
        row["skill_artifact_digest"]
        for row in plan["invocations"]
        if row["arm"] == "candidate"
    } == {"sha256:a6a9a1ea9c5337c2d2eb6131e00604c9a08879fe1bae66fa787fbbf2f0fda5da"}
    assert json.loads(PUBLIC_KEY.read_text(encoding="utf-8"))[
        "execution_authorized"
    ] is False
    public = json.loads(PUBLIC_KEY.read_text(encoding="utf-8"))
    assert public["actor_id"] == "github:ed3c"
    assert public["authority_id"] == "repository-owner-review@1"


def test_landed_preregistration_replays_without_private_key_or_provider() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--task-bundle",
            str(TASKS),
            "--protocol",
            str(PROTOCOL),
            "--provider-policy",
            str(PROVIDER_POLICY),
            "--environment-receipt",
            str(ENVIRONMENT_RECEIPT),
            "--plan",
            str(PLAN),
            "--public-key",
            str(PUBLIC_KEY),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "invocations=30" in completed.stdout
    assert "execution_authorized=false" in completed.stdout


def test_environment_receipt_rejects_verifier_byte_drift(tmp_path: Path) -> None:
    changed = json.loads(ENVIRONMENT_RECEIPT.read_text(encoding="utf-8"))
    changed["verifier_digest"] = "sha256:" + "0" * 64
    receipt = tmp_path / "environment.json"
    receipt.write_text(json.dumps(changed), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--task-bundle", str(TASKS),
            "--protocol", str(PROTOCOL),
            "--provider-policy", str(PROVIDER_POLICY),
            "--environment-receipt", str(receipt),
            "--plan", str(PLAN),
            "--public-key", str(PUBLIC_KEY),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "verifier_digest mismatch" in completed.stderr


@pytest.mark.parametrize(
    ("registry_name", "mutate", "expected"),
    [
        (
            "environment",
            lambda value: value.update(actor_id="github:untrusted"),
            "environment trust authority",
        ),
        (
            "environment",
            lambda value: value.update(active_receipt_ids="not-a-list"),
            "unique string list",
        ),
        (
            "plan",
            lambda value: value.update(authority_id="self-asserted"),
            "plan trust authority",
        ),
        (
            "plan",
            lambda value: value.update(revoked_key_ids=[next(iter(value["active_keys"]))]),
            "trust states overlap",
        ),
    ],
)
def test_trust_registries_reject_redigested_authority_type_and_state_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry_name: str,
    mutate,
    expected: str,
) -> None:
    module = _checker_module()
    arena = tmp_path / "data/arena"
    arena.mkdir(parents=True)
    if registry_name == "environment":
        source = ROOT / "data/arena/quote-repair-environment-trust.json"
        target = arena / source.name
        value = json.loads(source.read_text(encoding="utf-8"))
        mutate(value)
        value["registry_digest"] = sha256_json(
            {key: item for key, item in value.items() if key != "registry_digest"}
        )
        target.write_text(json.dumps(value), encoding="utf-8")
        receipt = json.loads(ENVIRONMENT_RECEIPT.read_text(encoding="utf-8"))
        receipt["trust_registry_digest_at_observation"] = value["registry_digest"]
        monkeypatch.setattr(module, "ROOT", tmp_path)
        with pytest.raises(ExperimentError, match=expected):
            module._verify_environment_trust(receipt)
    else:
        source = ROOT / "data/arena/quote-repair-plan-trust.json"
        target = arena / source.name
        value = json.loads(source.read_text(encoding="utf-8"))
        mutate(value)
        value["registry_digest"] = sha256_json(
            {key: item for key, item in value.items() if key != "registry_digest"}
        )
        target.write_text(json.dumps(value), encoding="utf-8")
        public = json.loads(PUBLIC_KEY.read_text(encoding="utf-8"))
        public["trust_registry_digest_at_issue"] = value["registry_digest"]
        monkeypatch.setattr(module, "ROOT", tmp_path)
        with pytest.raises(ExperimentError, match=expected):
            module._current_trust(public)


def test_plan_trust_rejects_false_mint_time_digest() -> None:
    module = _checker_module()
    public = json.loads(PUBLIC_KEY.read_text(encoding="utf-8"))
    public["trust_registry_digest_at_issue"] = "sha256:" + "0" * 64

    with pytest.raises(ExperimentError, match="mint-time trust registry mismatch"):
        module._current_trust(public)


def test_environment_verifier_requires_exact_anchor_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = load_quote_repair_task_bundle(TASKS)["tasks"][0]
    app = tmp_path / "app"
    logs = tmp_path / "logs/verifier"
    app.mkdir()
    logs.mkdir(parents=True)
    (app / "task.json").write_text(json.dumps(task), encoding="utf-8")
    verifier_path = ROOT / "data/arena/quote-repair-environment/verifier.py"
    spec = importlib.util.spec_from_file_location("quote_repair_verifier", verifier_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real_path = Path

    def redirected_path(value: str) -> Path:
        if value.startswith("/app/"):
            return app / Path(value).name
        if value.startswith("/logs/verifier/"):
            return logs / Path(value).name
        return real_path(value)

    monkeypatch.setattr(module, "Path", redirected_path)
    (app / "wiki.md").write_text(
        f"`{task['expected_repair_quote']}`\n", encoding="utf-8"
    )
    assert module.main() == 0
    assert json.loads((logs / "diagnostics.json").read_text())["status"] == (
        "wrong_file_or_span"
    )

    expected = task["wiki_template"].replace(
        f"`{task['planted_quote']}`", f"`{task['expected_repair_quote']}`"
    )
    (app / "wiki.md").write_text(expected, encoding="utf-8")
    assert module.main() == 0
    assert (logs / "reward.txt").read_text() == "1"
    assert json.loads((logs / "diagnostics.json").read_text())["status"] == (
        "succeeded"
    )

    (app / "adapter-diagnostic.json").write_text(
        json.dumps(
            {
                "schema_version": "quote-repair-adapter-diagnostic@1",
                "task_id": task["task_id"],
                "status": "no_candidate",
                "repair_attempts": 0,
                "detail": "bounded search produced no replacement candidate",
            }
        ),
        encoding="utf-8",
    )
    assert module.main() == 0
    diagnostic = json.loads((logs / "diagnostics.json").read_text())
    assert diagnostic == {
        "schema_version": "quote-repair-verifier@1",
        "status": "no_candidate",
        "reward": 0,
    }

    (app / "adapter-diagnostic.json").write_text("{}", encoding="utf-8")
    assert module.main() == 0
    assert json.loads((logs / "diagnostics.json").read_text())["status"] == (
        "malformed_repair"
    )
