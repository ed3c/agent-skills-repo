from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from arena_adapters.skillsbench.common import canonical_bytes, sha256_bytes
from arena_adapters.skillsbench.execution_image import (
    attach_environment_image_identity,
    bind_execution_parity_with_environment_image,
    load_environment_image_identity,
)
from arena_adapters.skillsbench.models import SkillsBenchAdapterError
from arena_adapters.skillsbench.parity import report_digest

ROOT = Path(__file__).resolve().parents[1]
EXECUTION_SCHEMA = ROOT / "contracts/skillsbench-execution-evidence.schema.json"
IMAGE_DIGEST = "sha256:" + "a" * 64
OTHER_IMAGE_DIGEST = "sha256:" + "b" * 64
BUNDLE_DIGEST = "sha256:" + "2" * 64
TASK_DIGEST = "sha256:" + "3" * 64
FIXTURE_DIGEST = "sha256:" + "4" * 64
DIAGNOSTICS_DIGEST = "sha256:" + "5" * 64


def write_images(
    path: Path,
    *,
    source: str = IMAGE_DIGEST,
    normalized: str = IMAGE_DIGEST,
    identical: bool = True,
    network_mode: str = "public",
) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": "skillsbench-environment-images@1",
                "source_image_id": source,
                "normalized_image_id": normalized,
                "identical": identical,
                "docker_server_version": "28.0.4",
                "network_mode": network_mode,
            }
        ),
        encoding="utf-8",
    )
    return path


def legacy_evidence(surface: str) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "skillsbench-execution-evidence@1",
        "task_id": "probe-task",
        "bundle_digest": BUNDLE_DIGEST,
        "surface": surface,
        "upstream": {
            "repository": "benchflow-ai/skillsbench",
            "commit": "1" * 40,
        },
        "execution": {
            "benchflow_version": "0.6.3",
            "agent": "oracle",
            "sandbox": "docker",
        },
        "task_check_passed": True,
        "oracle": {
            "result_digest": "sha256:" + ("6" if surface == "upstream" else "7") * 64,
            "task_digest": TASK_DIGEST,
            "reward": 1.0,
            "error": None,
            "verifier_error": None,
        },
        "verifier_probe": {
            "input_digest": FIXTURE_DIGEST,
            "reward": 1.0,
            "diagnostics_class": "pass",
            "diagnostics_digest": DIAGNOSTICS_DIGEST,
        },
    }
    value["evidence_digest"] = sha256_bytes(canonical_bytes(value))
    return value


def parity_report() -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "skillsbench-parity-report@1",
        "task_id": "probe-task",
        "bundle_digest": BUNDLE_DIGEST,
        "status": "known_loss",
        "ranking_eligible": False,
        "structural": {"status": "equivalent"},
        "execution": {"status": "not_run"},
        "known_losses": ["execution parity evidence is absent"],
    }
    value["report_digest"] = report_digest(value)
    return value


def attach(
    tmp_path: Path,
    surface: str,
    *,
    image_path: Path | None = None,
) -> dict[str, object]:
    path = image_path or write_images(tmp_path / "environment-images.json")
    return attach_environment_image_identity(
        legacy_evidence(surface),
        image_identity_path=path,
        surface=surface,
    )


def test_attachment_satisfies_schema_and_recomputes_digest(tmp_path: Path) -> None:
    evidence = attach(tmp_path, "upstream")
    assert evidence["execution"]["environment_image_digest"] == IMAGE_DIGEST  # type: ignore[index]
    expected = sha256_bytes(
        canonical_bytes(
            {key: value for key, value in evidence.items() if key != "evidence_digest"}
        )
    )
    assert evidence["evidence_digest"] == expected
    schema = json.loads(EXECUTION_SCHEMA.read_text(encoding="utf-8"))
    assert list(Draft202012Validator(schema).iter_errors(evidence)) == []


def test_equal_images_are_bound_into_equivalent_parity(tmp_path: Path) -> None:
    upstream = attach(tmp_path, "upstream")
    normalized = attach(tmp_path, "normalized")
    result = bind_execution_parity_with_environment_image(
        parity_report(), upstream, normalized
    )
    assert result["status"] == "equivalent"
    assert result["ranking_eligible"] is True
    execution = result["execution"]
    assert execution["environment_image_digest"] == IMAGE_DIGEST
    assert execution["same_environment_image_digest"] is True
    assert execution["upstream_evidence_digest"] == upstream["evidence_digest"]
    assert execution["normalized_evidence_digest"] == normalized["evidence_digest"]
    assert result["report_digest"] == report_digest(result)


def test_different_environment_images_fail_closed(tmp_path: Path) -> None:
    upstream = attach(tmp_path, "upstream")
    normalized = attach(tmp_path, "normalized")
    changed = deepcopy(normalized)
    changed["execution"]["environment_image_digest"] = OTHER_IMAGE_DIGEST  # type: ignore[index]
    changed.pop("evidence_digest")
    changed["evidence_digest"] = sha256_bytes(canonical_bytes(changed))
    with pytest.raises(SkillsBenchAdapterError, match="different environment image"):
        bind_execution_parity_with_environment_image(
            parity_report(), upstream, changed
        )


def test_tampered_image_binding_without_digest_update_is_rejected(
    tmp_path: Path,
) -> None:
    upstream = attach(tmp_path, "upstream")
    normalized = attach(tmp_path, "normalized")
    normalized["execution"]["environment_image_digest"] = OTHER_IMAGE_DIGEST  # type: ignore[index]
    with pytest.raises(SkillsBenchAdapterError, match="evidence_digest mismatch"):
        bind_execution_parity_with_environment_image(
            parity_report(), upstream, normalized
        )


def test_environment_image_document_must_prove_equality(tmp_path: Path) -> None:
    path = write_images(
        tmp_path / "environment-images.json",
        normalized=OTHER_IMAGE_DIGEST,
        identical=False,
    )
    with pytest.raises(SkillsBenchAdapterError, match="identities differ"):
        load_environment_image_identity(path)


def test_environment_image_document_rejects_unknown_network_mode(
    tmp_path: Path,
) -> None:
    path = write_images(
        tmp_path / "environment-images.json",
        network_mode="surprise",
    )
    with pytest.raises(SkillsBenchAdapterError, match="network_mode"):
        load_environment_image_identity(path)


def test_attachment_rejects_unknown_legacy_execution_shape(tmp_path: Path) -> None:
    value = legacy_evidence("upstream")
    value["execution"]["extra"] = True  # type: ignore[index]
    value.pop("evidence_digest")
    value["evidence_digest"] = sha256_bytes(canonical_bytes(value))
    with pytest.raises(SkillsBenchAdapterError, match="unknown shape"):
        attach_environment_image_identity(
            value,
            image_identity_path=write_images(tmp_path / "environment-images.json"),
            surface="upstream",
        )
