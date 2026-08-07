#!/usr/bin/env bash
set -euo pipefail

required=(
  TASK_ID
  SOURCE_TASK
  NORMALIZED_TASK
  BUNDLE_PATH
  EVIDENCE_ROOT
  BENCH_BIN
  BENCHFLOW_VERSION
  SKILLSBENCH_SHA
  RUN_IDENTITY
)
for name in "${required[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    echo "missing required environment variable: $name" >&2
    exit 64
  fi
done

mkdir -p \
  "$EVIDENCE_ROOT/checks" \
  "$EVIDENCE_ROOT/jobs/source" \
  "$EVIDENCE_ROOT/jobs/normalized" \
  "$EVIDENCE_ROOT/logs" \
  "$EVIDENCE_ROOT/fixture" \
  "$EVIDENCE_ROOT/probes/upstream/logs" \
  "$EVIDENCE_ROOT/probes/normalized/logs"

if [[ -f "${BENCHFLOW_ENVIRONMENT_FILE:-}" ]]; then
  cp "$BENCHFLOW_ENVIRONMENT_FILE" "$EVIDENCE_ROOT/benchflow-environment.txt"
fi
cp "$BUNDLE_PATH/bundle.json" "$EVIDENCE_ROOT/"
cp "$BUNDLE_PATH/parity.json" "$EVIDENCE_ROOT/parity-before.json"

readarray -t bundle_values < <(
  python - "$BUNDLE_PATH/bundle.json" <<'PY'
import json
import pathlib
import sys

bundle = json.loads(pathlib.Path(sys.argv[1]).read_text())
print(bundle['bundle_digest'])
print(bundle['task']['network_mode'])
PY
)
bundle_digest="${bundle_values[0]}"
network_mode="${bundle_values[1]}"
case "$network_mode" in
  public)
    network_args=()
    ;;
  no-network)
    network_args=(--network none)
    ;;
  *)
    echo "unsupported task network mode: $network_mode" >&2
    exit 65
    ;;
esac

source_image="arena-source-${TASK_ID}:${RUN_IDENTITY}"
normalized_image="arena-normalized-${TASK_ID}:${RUN_IDENTITY}"
fixture_container="arena-fixture-${TASK_ID}-${RUN_IDENTITY}"
source_probe_container="arena-probe-source-${TASK_ID}-${RUN_IDENTITY}"
normalized_probe_container="arena-probe-normalized-${TASK_ID}-${RUN_IDENTITY}"
cleanup() {
  docker rm -f \
    "$fixture_container" \
    "$source_probe_container" \
    "$normalized_probe_container" \
    >/dev/null 2>&1 || true
}
trap cleanup EXIT

started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
source_check_rc=0
normalized_check_rc=0
source_oracle_rc=0
normalized_oracle_rc=0
source_probe_rc=0
normalized_probe_rc=0
fixture_oracle_rc=0

"$BENCH_BIN" tasks check "$SOURCE_TASK" \
  > "$EVIDENCE_ROOT/checks/source.log" 2>&1 || source_check_rc=$?
"$BENCH_BIN" tasks check "$NORMALIZED_TASK" \
  > "$EVIDENCE_ROOT/checks/normalized.log" 2>&1 || normalized_check_rc=$?

"$BENCH_BIN" eval run \
  --tasks-dir "$SOURCE_TASK" \
  --agent oracle \
  --sandbox docker \
  --usage-tracking off \
  --concurrency 1 \
  --jobs-dir "$EVIDENCE_ROOT/jobs/source" \
  > "$EVIDENCE_ROOT/logs/source-oracle.log" 2>&1 || source_oracle_rc=$?

"$BENCH_BIN" eval run \
  --tasks-dir "$NORMALIZED_TASK" \
  --agent oracle \
  --sandbox docker \
  --usage-tracking off \
  --concurrency 1 \
  --jobs-dir "$EVIDENCE_ROOT/jobs/normalized" \
  > "$EVIDENCE_ROOT/logs/normalized-oracle.log" 2>&1 || normalized_oracle_rc=$?

docker build --quiet --tag "$source_image" "$SOURCE_TASK/environment" \
  > "$EVIDENCE_ROOT/logs/source-image-build.log" 2>&1
docker build --quiet --tag "$normalized_image" "$NORMALIZED_TASK/environment" \
  > "$EVIDENCE_ROOT/logs/normalized-image-build.log" 2>&1
source_image_id="$(docker image inspect --format '{{.Id}}' "$source_image")"
normalized_image_id="$(docker image inspect --format '{{.Id}}' "$normalized_image")"
docker_version="$(docker version --format '{{.Server.Version}}')"

SOURCE_IMAGE_ID="$source_image_id" \
NORMALIZED_IMAGE_ID="$normalized_image_id" \
DOCKER_VERSION="$docker_version" \
NETWORK_MODE="$network_mode" \
python - <<'PY'
import json
import os
import pathlib

root = pathlib.Path(os.environ['EVIDENCE_ROOT'])
value = {
    'schema_version': 'skillsbench-environment-images@1',
    'source_image_id': os.environ['SOURCE_IMAGE_ID'],
    'normalized_image_id': os.environ['NORMALIZED_IMAGE_ID'],
    'identical': os.environ['SOURCE_IMAGE_ID'] == os.environ['NORMALIZED_IMAGE_ID'],
    'docker_server_version': os.environ['DOCKER_VERSION'],
    'network_mode': os.environ['NETWORK_MODE'],
}
(root / 'environment-images.json').write_text(
    json.dumps(value, indent=2, sort_keys=True) + '\n'
)
PY
if [[ "$source_image_id" != "$normalized_image_id" ]]; then
  echo "source and normalized environment image IDs differ" >&2
  exit 31
fi

docker create \
  --name "$fixture_container" \
  "${network_args[@]}" \
  --mount "type=bind,src=$SOURCE_TASK/oracle,dst=/oracle,readonly" \
  "$source_image" \
  bash /oracle/solve.sh \
  > "$EVIDENCE_ROOT/logs/fixture-container-id.txt"
docker start -a "$fixture_container" \
  > "$EVIDENCE_ROOT/logs/fixture-oracle.log" 2>&1 || true
fixture_oracle_rc="$(docker inspect --format '{{.State.ExitCode}}' "$fixture_container")"
if [[ "$fixture_oracle_rc" -ne 0 ]]; then
  echo "fixture oracle failed: $fixture_oracle_rc" >&2
  exit 32
fi

mapfile -t output_paths < <(
  python scripts/import_skillsbench_tasks.py probe-paths --task-id "$TASK_ID"
)
if [[ "${#output_paths[@]}" -eq 0 ]]; then
  echo "execution probe policy returned no output paths" >&2
  exit 33
fi
for output_path in "${output_paths[@]}"; do
  host_path="$EVIDENCE_ROOT/fixture/${output_path#/}"
  mkdir -p "$(dirname "$host_path")"
  docker cp "$fixture_container:$output_path" "$host_path"
done

docker create \
  --name "$source_probe_container" \
  "${network_args[@]}" \
  --mount "type=bind,src=$SOURCE_TASK/verifier,dst=/verifier,readonly" \
  --mount "type=bind,src=$EVIDENCE_ROOT/probes/upstream/logs,dst=/logs" \
  "$source_image" \
  bash /verifier/test.sh \
  > "$EVIDENCE_ROOT/logs/source-probe-container-id.txt"
docker create \
  --name "$normalized_probe_container" \
  "${network_args[@]}" \
  --mount "type=bind,src=$NORMALIZED_TASK/verifier,dst=/verifier,readonly" \
  --mount "type=bind,src=$EVIDENCE_ROOT/probes/normalized/logs,dst=/logs" \
  "$normalized_image" \
  bash /verifier/test.sh \
  > "$EVIDENCE_ROOT/logs/normalized-probe-container-id.txt"

for output_path in "${output_paths[@]}"; do
  host_path="$EVIDENCE_ROOT/fixture/${output_path#/}"
  docker cp "$host_path" "$source_probe_container:$output_path"
  docker cp "$host_path" "$normalized_probe_container:$output_path"
done

docker start -a "$source_probe_container" \
  > "$EVIDENCE_ROOT/logs/source-verifier-probe.log" 2>&1 || true
source_probe_rc="$(docker inspect --format '{{.State.ExitCode}}' "$source_probe_container")"
docker start -a "$normalized_probe_container" \
  > "$EVIDENCE_ROOT/logs/normalized-verifier-probe.log" 2>&1 || true
normalized_probe_rc="$(docker inspect --format '{{.State.ExitCode}}' "$normalized_probe_container")"

task_checks_passed=false
if [[ "$source_check_rc" -eq 0 && "$normalized_check_rc" -eq 0 ]]; then
  task_checks_passed=true
fi
extract_check_flag=()
if [[ "$task_checks_passed" == true ]]; then
  extract_check_flag+=(--task-check-passed)
fi

python scripts/import_skillsbench_tasks.py extract-execution \
  --bundle-json "$BUNDLE_PATH/bundle.json" \
  --surface upstream \
  --jobs-root "$EVIDENCE_ROOT/jobs/source" \
  --fixture-root "$EVIDENCE_ROOT/fixture" \
  --verifier-logs-root "$EVIDENCE_ROOT/probes/upstream/logs/verifier" \
  --benchflow-version "$BENCHFLOW_VERSION" \
  "${extract_check_flag[@]}" \
  --output "$EVIDENCE_ROOT/upstream-execution.json"

python scripts/import_skillsbench_tasks.py extract-execution \
  --bundle-json "$BUNDLE_PATH/bundle.json" \
  --surface normalized \
  --jobs-root "$EVIDENCE_ROOT/jobs/normalized" \
  --fixture-root "$EVIDENCE_ROOT/fixture" \
  --verifier-logs-root "$EVIDENCE_ROOT/probes/normalized/logs/verifier" \
  --benchflow-version "$BENCHFLOW_VERSION" \
  "${extract_check_flag[@]}" \
  --output "$EVIDENCE_ROOT/normalized-execution.json"

python scripts/import_skillsbench_tasks.py bind-execution \
  --parity-report "$BUNDLE_PATH/parity.json" \
  --upstream-evidence "$EVIDENCE_ROOT/upstream-execution.json" \
  --normalized-evidence "$EVIDENCE_ROOT/normalized-execution.json" \
  --output "$EVIDENCE_ROOT/parity-after.json"

completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
SOURCE_CHECK_RC="$source_check_rc" \
NORMALIZED_CHECK_RC="$normalized_check_rc" \
SOURCE_ORACLE_RC="$source_oracle_rc" \
NORMALIZED_ORACLE_RC="$normalized_oracle_rc" \
FIXTURE_ORACLE_RC="$fixture_oracle_rc" \
SOURCE_PROBE_RC="$source_probe_rc" \
NORMALIZED_PROBE_RC="$normalized_probe_rc" \
STARTED_AT="$started_at" \
COMPLETED_AT="$completed_at" \
BUNDLE_DIGEST="$bundle_digest" \
NETWORK_MODE="$network_mode" \
ENVIRONMENT_IMAGE_ID="$source_image_id" \
DOCKER_VERSION="$docker_version" \
python - <<'PY'
import hashlib
import json
import os
import pathlib

root = pathlib.Path(os.environ['EVIDENCE_ROOT'])
upstream = json.loads((root / 'upstream-execution.json').read_text())
normalized = json.loads((root / 'normalized-execution.json').read_text())
parity = json.loads((root / 'parity-after.json').read_text())
payload = {
    'schema_version': 'skillsbench-execution-parity-bundle@1',
    'task_id': os.environ['TASK_ID'],
    'bundle_digest': os.environ['BUNDLE_DIGEST'],
    'upstream_commit': os.environ['SKILLSBENCH_SHA'],
    'benchflow_version': os.environ['BENCHFLOW_VERSION'],
    'docker_server_version': os.environ['DOCKER_VERSION'],
    'environment_image_id': os.environ['ENVIRONMENT_IMAGE_ID'],
    'network_mode': os.environ['NETWORK_MODE'],
    'started_at': os.environ['STARTED_AT'],
    'completed_at': os.environ['COMPLETED_AT'],
    'returncodes': {
        'source_task_check': int(os.environ['SOURCE_CHECK_RC']),
        'normalized_task_check': int(os.environ['NORMALIZED_CHECK_RC']),
        'source_oracle': int(os.environ['SOURCE_ORACLE_RC']),
        'normalized_oracle': int(os.environ['NORMALIZED_ORACLE_RC']),
        'fixture_oracle': int(os.environ['FIXTURE_ORACLE_RC']),
        'source_verifier_probe': int(os.environ['SOURCE_PROBE_RC']),
        'normalized_verifier_probe': int(os.environ['NORMALIZED_PROBE_RC']),
    },
    'fixture_digest': upstream['verifier_probe']['input_digest'],
    'upstream_evidence_digest': upstream['evidence_digest'],
    'normalized_evidence_digest': normalized['evidence_digest'],
    'parity_report_digest': parity['report_digest'],
    'parity_status': parity['status'],
    'ranking_eligible': parity['ranking_eligible'],
}
raw = json.dumps(
    payload,
    ensure_ascii=False,
    sort_keys=True,
    separators=(',', ':'),
).encode()
payload['evidence_bundle_digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
(root / 'execution-parity-bundle.json').write_text(
    json.dumps(payload, indent=2, sort_keys=True) + '\n'
)
if parity['status'] != 'equivalent' or parity['ranking_eligible'] is not True:
    raise SystemExit('execution parity did not become equivalent')
if any(payload['returncodes'].values()):
    raise SystemExit(f"one or more recorded commands failed: {payload['returncodes']}")
PY

find "$EVIDENCE_ROOT" -type f -print | LC_ALL=C sort \
  > "$EVIDENCE_ROOT/file-list.txt"
find "$EVIDENCE_ROOT" -type f ! -name artifact-sha256.txt -print0 \
  | LC_ALL=C sort -z \
  | xargs -0 sha256sum \
  > "$EVIDENCE_ROOT/artifact-sha256.txt"
