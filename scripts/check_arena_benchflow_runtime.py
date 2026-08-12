#!/usr/bin/env python3
"""Verify one non-secret Arena BenchFlow runtime artifact offline."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import stat
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill_arena.experiment import (  # noqa: E402
    ExperimentError,
    load_runtime_policy,
    replay_bundle,
    summarize_paired_bundle,
    validate_catalog_evidence,
    validate_preparation,
)
from skill_arena.experiment.model import canonical_bytes, sha256_bytes  # noqa: E402

_SECRET_RE = re.compile(
    rb"(?:gh[opsu]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    rb"(?i:bearer)[ \t]+[A-Za-z0-9._~+/=-]{16,})"
)


def _load(path: Path, label: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ExperimentError(f"{label} must be a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentError(f"cannot read {label}: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ExperimentError(f"{label} root must be an object")
    return value


def _schema_validate(value: object, schema: object, label: str) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.absolute_path) or "<root>"
        raise ExperimentError(f"{label} schema failure at {path}: {first.message}")


def _all_regular_files(root: Path) -> dict[str, Path]:
    if root.is_symlink() or not root.is_dir():
        raise ExperimentError(f"runtime artifact root is absent or unsafe: {root}")
    files: dict[str, Path] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ExperimentError(f"runtime artifact contains symlink: {relative}")
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ExperimentError(f"runtime artifact contains special file: {relative}")
        files[relative] = path
    return files


def _public_keys(document: dict[str, object]) -> tuple[dict[str, bytes], dict[str, bytes]]:
    rows = document.get("keys")
    if not isinstance(rows, list) or len(rows) != 2:
        raise ExperimentError("runtime public key document must contain two keys")
    plan: dict[str, bytes] = {}
    bundle: dict[str, bytes] = {}
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ExperimentError("runtime public key row is invalid")
        key_id = row.get("key_id")
        purpose = row.get("purpose")
        encoded = row.get("public_key_base64")
        if not isinstance(key_id, str) or key_id in seen or not isinstance(encoded, str):
            raise ExperimentError("runtime public key identity is invalid or duplicated")
        seen.add(key_id)
        try:
            raw = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ExperimentError("runtime public key is not valid base64") from exc
        if len(raw) != 32:
            raise ExperimentError("runtime Ed25519 public key must contain 32 bytes")
        if purpose == "experiment-preregistration":
            plan[key_id] = raw
        elif purpose == "experiment-bundle":
            bundle[key_id] = raw
        else:
            raise ExperimentError("runtime public key purpose is unsupported")
    if len(plan) != 1 or len(bundle) != 1:
        raise ExperimentError("runtime public keys do not cover both trust domains")
    return plan, bundle


def verify_runtime(root: Path, schema_path: Path, expected_policy_path: Path) -> dict[str, object]:
    schema = _load(schema_path, "Arena BenchFlow runtime schema")
    files = _all_regular_files(root)
    required = {
        "runtime-policy.json",
        "model-catalog-evidence.json",
        "preparation.json",
        "public-keys.json",
        "preregistered-plan-envelope.json",
        "replay-result.json",
        "paired-result.json",
        "runtime-summary.json",
        "artifact-manifest.json",
    }
    missing = sorted(required - files.keys())
    if missing:
        raise ExperimentError(f"runtime artifact is missing required files: {missing}")

    for relative, path in files.items():
        data = path.read_bytes()
        if _SECRET_RE.search(data):
            raise ExperimentError(f"runtime artifact contains token-shaped secret: {relative}")

    runtime_policy_doc = _load(root / "runtime-policy.json", "runtime policy")
    expected_policy_doc = _load(expected_policy_path, "expected runtime policy")
    if runtime_policy_doc != expected_policy_doc:
        raise ExperimentError("runtime policy differs from the repository-pinned policy")
    _schema_validate(runtime_policy_doc, schema, "runtime policy")
    policy = load_runtime_policy(root / "runtime-policy.json")

    catalog = _load(root / "model-catalog-evidence.json", "model catalog evidence")
    preparation = _load(root / "preparation.json", "runtime preparation")
    paired = _load(root / "paired-result.json", "paired result")
    public_keys = _load(root / "public-keys.json", "public keys")
    summary = _load(root / "runtime-summary.json", "runtime summary")
    artifact_manifest = _load(root / "artifact-manifest.json", "artifact manifest")
    for label, value in (
        ("model catalog evidence", catalog),
        ("runtime preparation", preparation),
        ("paired result", paired),
        ("public keys", public_keys),
        ("runtime summary", summary),
        ("artifact manifest", artifact_manifest),
    ):
        _schema_validate(value, schema, label)

    validate_catalog_evidence(catalog, policy)
    validate_preparation(preparation, policy, catalog)

    bundle_path = summary.get("bundle_path")
    if not isinstance(bundle_path, str):
        raise ExperimentError("runtime summary bundle_path is invalid")
    bundle = root / bundle_path
    plan_keys, bundle_keys = _public_keys(public_keys)
    replay = replay_bundle(
        bundle,
        trusted_plan_keys=plan_keys,
        trusted_bundle_keys=bundle_keys,
    )
    recorded_replay = _load(root / "replay-result.json", "recorded replay result")
    if replay != recorded_replay:
        raise ExperimentError("recorded replay result differs from independent replay")
    independently_summarized = summarize_paired_bundle(bundle)
    if independently_summarized != paired:
        raise ExperimentError("paired result differs from independent materialization")
    if paired.get("ranking_claim_allowed") is not False:
        raise ExperimentError("single-task runtime may not authorize a ranking claim")
    arms = paired.get("arms")
    if not isinstance(arms, dict):
        raise ExperimentError("paired result arms are absent")
    for arm in ("baseline", "candidate"):
        row = arms.get(arm)
        if (
            not isinstance(row, dict)
            or row.get("planned") != policy.repetitions
            or row.get("scored") != policy.repetitions
        ):
            raise ExperimentError(f"paired result {arm} denominator is incomplete")

    expected_summary = {
        "task_id": policy.task_id,
        "agent": policy.agent,
        "model": policy.model,
        "model_catalog_version": catalog["version"],
        "bundle_manifest_hash": replay["manifest_hash"],
        "plan_digest": replay["plan_digest"],
        "preparation_digest": preparation["preparation_digest"],
        "paired_result_digest": paired["result_digest"],
        "ranking_claim_allowed": False,
    }
    if any(summary.get(key) != value for key, value in expected_summary.items()):
        raise ExperimentError("runtime summary binding mismatch")

    entries = artifact_manifest.get("files")
    if not isinstance(entries, list):
        raise ExperimentError("artifact manifest files are absent")
    expected_paths = sorted(path for path in files if path != "artifact-manifest.json")
    observed_paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ExperimentError("artifact manifest entry is invalid")
        relative = entry.get("path")
        if not isinstance(relative, str) or relative not in files:
            raise ExperimentError("artifact manifest path is invalid")
        data = files[relative].read_bytes()
        if entry.get("sha256") != sha256_bytes(data) or entry.get("size_bytes") != len(data):
            raise ExperimentError(f"artifact manifest digest or size mismatch: {relative}")
        observed_paths.append(relative)
    if observed_paths != expected_paths:
        raise ExperimentError("artifact manifest file set or ordering is invalid")
    manifest_without_digest = {
        "schema_version": "arena-runtime-artifact-manifest@1",
        "files": entries,
    }
    expected_manifest_digest = "sha256:" + hashlib.sha256(
        canonical_bytes(manifest_without_digest)
    ).hexdigest()
    if artifact_manifest.get("manifest_digest") != expected_manifest_digest:
        raise ExperimentError("artifact manifest digest mismatch")

    for path in root.rglob("arena-benchflow-evidence.json"):
        evidence = _load(path, "per-invocation BenchFlow evidence")
        _schema_validate(evidence, schema, path.relative_to(root).as_posix())
        if evidence.get("runtime_policy_digest") != policy.digest:
            raise ExperimentError("per-invocation runtime policy binding mismatch")
        if evidence.get("effective_policy_digest") != preparation["effective_policy_digest"]:
            raise ExperimentError("per-invocation effective policy binding mismatch")
        if evidence.get("environment_image_digest") != preparation["environment_image_digest"]:
            raise ExperimentError("per-invocation image binding mismatch")

    return {
        "status": "verified",
        "runtime_summary_digest": sha256_bytes(
            (root / "runtime-summary.json").read_bytes()
        ),
        "artifact_manifest_digest": artifact_manifest["manifest_digest"],
        "bundle_manifest_hash": replay["manifest_hash"],
        "paired_result_digest": paired["result_digest"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "--schema",
        type=Path,
        default=ROOT / "contracts/arena-benchflow-runtime.schema.json",
    )
    parser.add_argument(
        "--expected-policy",
        type=Path,
        default=ROOT / "data/arena/benchflow-dialogue-parser-github-models.json",
    )
    args = parser.parse_args()
    try:
        result = verify_runtime(args.root, args.schema, args.expected_policy)
    except (OSError, json.JSONDecodeError, ExperimentError) as exc:
        print(f"FAIL: Arena BenchFlow runtime evidence: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
