"""Single-attempt execution and signed content-addressed bundle materialization."""
from __future__ import annotations

import base64
import copy
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, cast

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .model import (
    BUNDLE_MANIFEST_SCHEMA,
    BUNDLE_SIGNATURE_DOMAIN,
    FAILURE_DENOMINATOR_POLICY,
    INVOCATION_MANIFEST_SCHEMA,
    METRICS_SCHEMA,
    OUTCOME_CLASSES,
    OUTCOME_SCHEMA,
    REQUIRED_EVIDENCE_FILES,
    RUN_INDEX_SCHEMA,
    SIGNED_BUNDLE_SCHEMA,
    TRAJECTORY_SCHEMA,
    VERIFIER_SCHEMA,
    WORKSPACE_POLICY,
    ExperimentAdapter,
    ExperimentError,
    InvocationCapture,
    _ID_RE,
    _METRICS_FIELDS,
    canonical_bytes,
    identifier,
    regular_file,
    sha256_bytes,
    sha256_json,
    validate_relative_artifact_path,
)
from .plan import verify_plan_envelope

_TRAJECTORY_FIELDS = {"schema_version", "events"}
_VERIFIER_FIELDS = {"schema_version", "status", "reward", "diagnostics_digest"}


def _utc_timestamp(value: datetime, label: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExperimentError(f"{label} must include a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        __import__("json").dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _validate_metrics(metrics: Mapping[str, object]) -> dict[str, object]:
    if set(metrics) != _METRICS_FIELDS or metrics.get("schema_version") != METRICS_SCHEMA:
        raise ExperimentError("experiment metrics schema or fields are invalid")
    for field in _METRICS_FIELDS - {"schema_version"}:
        value = metrics[field]
        if type(value) is not int or cast(int, value) < 0:
            raise ExperimentError(
                f"experiment metric {field} must be a non-negative integer"
            )
    return dict(metrics)


def _validate_trajectory(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != _TRAJECTORY_FIELDS or value.get("schema_version") != TRAJECTORY_SCHEMA:
        raise ExperimentError("structured trajectory schema or fields are invalid")
    events = value.get("events")
    if not isinstance(events, list) or not all(isinstance(item, dict) for item in events):
        raise ExperimentError("structured trajectory events must be a list of objects")
    return copy.deepcopy(dict(value))


def _validate_verifier(value: Mapping[str, object], reward: float | None) -> dict[str, object]:
    if set(value) != _VERIFIER_FIELDS or value.get("schema_version") != VERIFIER_SCHEMA:
        raise ExperimentError("verifier evidence schema or fields are invalid")
    status = value.get("status")
    if not isinstance(status, str) or not status:
        raise ExperimentError("verifier evidence status is invalid")
    verifier_reward = value.get("reward")
    if verifier_reward != reward:
        raise ExperimentError("verifier reward does not match invocation reward")
    diagnostics = value.get("diagnostics_digest")
    if diagnostics is not None and (
        not isinstance(diagnostics, str)
        or not diagnostics.startswith("sha256:")
        or len(diagnostics) != 71
    ):
        raise ExperimentError("verifier diagnostics_digest is invalid")
    return dict(value)


def _validate_capture(capture: InvocationCapture, invocation_id: str) -> None:
    if capture.classification not in OUTCOME_CLASSES:
        raise ExperimentError("adapter returned an unknown outcome classification")
    reward_classes = {"succeeded", "task_failure"}
    if capture.classification in reward_classes:
        if (
            isinstance(capture.reward, bool)
            or not isinstance(capture.reward, (int, float))
            or not 0.0 <= float(capture.reward) <= 1.0
        ):
            raise ExperimentError(
                f"{capture.classification} invocation must include reward in 0..1"
            )
    elif capture.reward is not None:
        raise ExperimentError(
            f"{capture.classification} invocation cannot carry a verifier reward"
        )
    if capture.classification == "succeeded":
        if capture.error_code is not None:
            raise ExperimentError("successful invocation cannot carry error_code")
    elif not isinstance(capture.error_code, str) or not capture.error_code:
        raise ExperimentError("non-success invocation requires error_code")
    if capture.adapter_exit_code is not None and type(capture.adapter_exit_code) is not int:
        raise ExperimentError("adapter_exit_code must be an integer or null")
    if not isinstance(capture.stdout, bytes) or not isinstance(capture.stderr, bytes):
        raise ExperimentError("adapter stdout/stderr must be bytes")
    if not isinstance(capture.trajectory, Mapping):
        raise ExperimentError(f"invocation {invocation_id} is missing structured trajectory")
    if not isinstance(capture.verifier, Mapping):
        raise ExperimentError(f"invocation {invocation_id} is missing verifier evidence")
    _validate_trajectory(capture.trajectory)
    _validate_verifier(capture.verifier, capture.reward)
    _validate_metrics(capture.metrics)
    if not isinstance(capture.artifacts, Mapping):
        raise ExperimentError("adapter artifacts must be a mapping")
    for path, data in capture.artifacts.items():
        validate_relative_artifact_path(path)
        if not isinstance(data, bytes):
            raise ExperimentError("adapter artifact values must be bytes")
    if capture.started_at.tzinfo is None or capture.completed_at.tzinfo is None:
        raise ExperimentError("capture timestamps must include timezone")
    if capture.completed_at < capture.started_at:
        raise ExperimentError("capture completed before it started")


def _failure_capture(exc: Exception, now: datetime) -> InvocationCapture:
    exception_class = type(exc).__name__
    return InvocationCapture(
        classification="infrastructure_failure",
        reward=None,
        adapter_exit_code=None,
        error_code=exception_class,
        stdout=b"",
        # Provider messages may contain credentials. Only the class crosses this boundary.
        stderr=(f"adapter exception: {exception_class}\n").encode("utf-8"),
        trajectory={
            "schema_version": TRAJECTORY_SCHEMA,
            "events": [
                {
                    "type": "adapter_exception",
                    "exception_class": exception_class,
                }
            ],
        },
        verifier={
            "schema_version": VERIFIER_SCHEMA,
            "status": "not_run_infrastructure_failure",
            "reward": None,
            "diagnostics_digest": None,
        },
        metrics={
            "schema_version": METRICS_SCHEMA,
            "end_to_end_latency_ms": 0,
            "verifier_latency_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_tokens": 0,
            "cost_microunits": 0,
            "cpu_time_ms": 0,
            "peak_memory_bytes": 0,
            "tool_call_count": 0,
        },
        artifacts={},
        started_at=now,
        completed_at=now,
    )


def _materialize_invocation(
    root: Path,
    invocation: Mapping[str, object],
    capture: InvocationCapture,
    *,
    workspace_nonce: str,
) -> tuple[str, str]:
    invocation_id = cast(str, invocation["invocation_id"])
    target = root / invocation_id
    target.mkdir()
    _write_json(target / "invocation.json", invocation)
    (target / "stdout.bin").write_bytes(capture.stdout)
    (target / "stderr.bin").write_bytes(capture.stderr)
    _write_json(target / "trajectory.json", _validate_trajectory(capture.trajectory))
    _write_json(
        target / "verifier.json",
        _validate_verifier(capture.verifier, capture.reward),
    )
    _write_json(target / "metrics.json", _validate_metrics(capture.metrics))

    artifact_paths: list[str] = []
    for relative, data in sorted(capture.artifacts.items()):
        safe_relative = validate_relative_artifact_path(relative)
        artifact_path = target / "artifacts" / safe_relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(data)
        artifact_paths.append(f"artifacts/{safe_relative}")

    outcome = {
        "schema_version": OUTCOME_SCHEMA,
        "invocation_id": invocation_id,
        "classification": capture.classification,
        "reward": capture.reward,
        "adapter_exit_code": capture.adapter_exit_code,
        "error_code": capture.error_code,
        "started_at": _utc_timestamp(capture.started_at, "started_at"),
        "completed_at": _utc_timestamp(capture.completed_at, "completed_at"),
        "workspace_nonce": workspace_nonce,
        "workspace_policy": WORKSPACE_POLICY,
        "cleanup_verified": True,
        "attempt_number": 1,
        "retry_count": 0,
        "artifact_file_count": len(artifact_paths),
    }
    _write_json(target / "outcome.json", outcome)

    files: list[dict[str, object]] = []
    for name in sorted((*REQUIRED_EVIDENCE_FILES, *artifact_paths)):
        data = regular_file(target / name, f"invocation evidence {name}")
        files.append(
            {
                "path": name,
                "sha256": sha256_bytes(data),
                "size_bytes": len(data),
            }
        )
    manifest_without_digest = {
        "schema_version": INVOCATION_MANIFEST_SCHEMA,
        "invocation_id": invocation_id,
        "classification": capture.classification,
        "files": files,
    }
    manifest = {
        **manifest_without_digest,
        "manifest_digest": sha256_json(manifest_without_digest),
    }
    _write_json(target / "invocation-manifest.json", manifest)
    return cast(str, manifest["manifest_digest"]), capture.classification


def run_experiment(
    plan_envelope: Mapping[str, object],
    *,
    trusted_plan_keys: Mapping[str, bytes],
    adapter: ExperimentAdapter,
    output_dir: Path | str,
    bundle_private_key: Ed25519PrivateKey,
    bundle_issuer_key_id: str,
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    nonce_fn: Callable[[], str] = lambda: uuid.uuid4().hex,
) -> Path:
    """Execute exactly one attempt per preregistered invocation and sign the bundle.

    Adapter exceptions become explicit ``infrastructure_failure`` outcomes and remain
    in the denominator. Cleanup failure is different: the runner refuses to sign a
    bundle whose disposable-workspace guarantee cannot be proven.
    """
    plan = verify_plan_envelope(plan_envelope, trusted_plan_keys)
    output_root = Path(output_dir)
    if output_root.exists() and (
        output_root.is_symlink() or not output_root.is_dir()
    ):
        raise ExperimentError(
            f"experiment output root is not a directory: {output_root}"
        )
    output_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=".arena-experiment.", dir=output_root)
    )
    try:
        _write_json(temporary / "plan-envelope.json", plan_envelope)
        invocations_root = temporary / "invocations"
        invocations_root.mkdir()
        workspace_root = temporary / ".workspaces"
        workspace_root.mkdir()
        records: list[dict[str, object]] = []
        outcome_counts = {name: 0 for name in sorted(OUTCOME_CLASSES)}
        seen_nonces: set[str] = set()

        for invocation in cast(list[dict[str, object]], plan["invocations"]):
            invocation_id = cast(str, invocation["invocation_id"])
            workspace_nonce = nonce_fn()
            if (
                not isinstance(workspace_nonce, str)
                or _ID_RE.fullmatch(workspace_nonce) is None
                or workspace_nonce in seen_nonces
            ):
                raise ExperimentError("workspace nonce is invalid or reused")
            seen_nonces.add(workspace_nonce)
            workspace = workspace_root / workspace_nonce
            workspace.mkdir()
            if any(workspace.iterdir()):
                raise ExperimentError("fresh experiment workspace is not empty")
            try:
                try:
                    capture = adapter.execute(copy.deepcopy(invocation), workspace)
                except Exception as exc:  # noqa: BLE001 - converted to typed outcome
                    capture = _failure_capture(exc, now_fn())
                _validate_capture(capture, invocation_id)
            finally:
                try:
                    shutil.rmtree(workspace)
                except OSError as exc:
                    raise ExperimentError(
                        f"cannot destroy workspace for {invocation_id}: {exc}"
                    ) from exc
                if workspace.exists():
                    raise ExperimentError(
                        f"workspace destruction is not proven for {invocation_id}"
                    )
            manifest_digest, classification = _materialize_invocation(
                invocations_root,
                invocation,
                capture,
                workspace_nonce=workspace_nonce,
            )
            outcome_counts[classification] += 1
            records.append(
                {
                    "invocation_id": invocation_id,
                    "manifest_digest": manifest_digest,
                    "classification": classification,
                }
            )

        shutil.rmtree(workspace_root)
        execution_sequence = [
            cast(str, item["invocation_id"])
            for item in cast(list[dict[str, object]], plan["invocations"])
        ]
        run_index_without_digest = {
            "schema_version": RUN_INDEX_SCHEMA,
            "plan_digest": plan["plan_digest"],
            "failure_denominator_policy": FAILURE_DENOMINATOR_POLICY,
            "planned_invocation_count": plan["planned_invocation_count"],
            "recorded_invocation_count": len(records),
            "execution_sequence": execution_sequence,
            "records": records,
            "outcome_counts": outcome_counts,
        }
        run_index = {
            **run_index_without_digest,
            "run_index_digest": sha256_json(run_index_without_digest),
        }
        _write_json(temporary / "run-index.json", run_index)

        bundle_payload = {
            "schema_version": BUNDLE_MANIFEST_SCHEMA,
            "experiment_id": cast(dict[str, object], plan["spec"])[
                "experiment_id"
            ],
            "plan_digest": plan["plan_digest"],
            "plan_hash": plan_envelope["plan_hash"],
            "plan_envelope_digest": sha256_json(plan_envelope),
            "run_index_digest": run_index["run_index_digest"],
            "planned_invocation_count": plan["planned_invocation_count"],
            "recorded_invocation_count": len(records),
            "invocation_manifests": records,
            "failure_denominator_policy": FAILURE_DENOMINATOR_POLICY,
            "issued_at": _utc_timestamp(now_fn(), "bundle issued_at"),
        }
        _write_json(temporary / "bundle-manifest.json", bundle_payload)
        raw = canonical_bytes(bundle_payload)
        bundle_envelope = {
            "schema_version": SIGNED_BUNDLE_SCHEMA,
            "payload": bundle_payload,
            "manifest_hash": sha256_bytes(raw),
            "issuer_key_id": identifier(
                bundle_issuer_key_id,
                "bundle_issuer_key_id",
            ),
            "signature": base64.b64encode(
                bundle_private_key.sign(BUNDLE_SIGNATURE_DOMAIN + raw)
            ).decode("ascii"),
        }
        _write_json(temporary / "bundle-envelope.json", bundle_envelope)
        destination = output_root / (
            "sha256-"
            + cast(str, bundle_envelope["manifest_hash"]).removeprefix("sha256:")
        )
        if destination.exists():
            raise ExperimentError(
                f"content-addressed experiment bundle already exists: {destination}"
            )
        os.replace(temporary, destination)
        return destination
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


__all__ = ["run_experiment"]
