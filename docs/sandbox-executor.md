# OpenShell sandbox executor contract

This package implements the contract half of issue #3 and a concrete OpenShell 0.0.59 CLI adapter.
It is not qualification evidence until a real gateway-backed run is executed, reviewed, and landed.

## Evidence boundary

A successful invocation must produce all of the following in one atomic directory:

```text
<bundle>/
├── receipt.json          # Ed25519-signed sandbox-case-receipt@1
├── attestation.json      # image, policy, limits, transport, cleanup evidence
├── result.json           # task output hashes and physical timestamps
└── bundle-manifest.json  # content digest for the other three files
```

The executor writes nothing when configuration, gateway, substrate, transport, evidence, cleanup,
wall-window, or preregistered output checks fail. A task result is signed only after sandbox deletion
and backing-container absence are verified.

## Pinned envelope

The first profile is deliberately narrow:

- OpenShell package version: `0.0.59`
- source reference: `NVIDIA/OpenShell@v0.0.59`
- substrate: Docker
- transport: trailing command on `openshell sandbox create`
- network: deny all
- uploaded input: read-only policy path
- output/work: disposable writable paths
- provider auto-discovery: disabled
- signing key: development-scoped, owner-only, and outside repository history

OpenShell 0.0.59 does not expose the newer stable `sandbox exec` shape in its pinned CLI reference.
The adapter uploads a self-contained runner and case input, executes the runner as the create command,
downloads `/sandbox/output`, retrieves the effective policy, inspects the backing Docker container,
and deletes the sandbox in `finally`.

## Control flow

```text
validate profile/case/key/output path
        │
        ▼
probe exact OpenShell version + gateway + Docker server
        │
        ▼
create fresh sandbox with unique name and workspace nonce
        │
        ▼
run uploaded runner with scrubbed environment and RLIMIT controls
        │
        ▼
retrieve result + effective policy + Docker image/limit evidence
        │
        ▼
delete sandbox and verify container absence
        │
        ├── any failure: no signed bundle
        ▼
validate preregistered deterministic result
        │
        ▼
sign receipt using domain-separated Ed25519 and atomically publish bundle
```

## Development key

Create a raw development key outside the repository. Never place it in a checkout, CI artifact, or
GitHub secret intended for production.

```sh
umask 077
python - <<'PY'
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

Path.home().joinpath('.config/agent-skills-arena').mkdir(parents=True, exist_ok=True)
path = Path.home() / '.config/agent-skills-arena/dev-sandbox-ed25519.key'
path.write_bytes(Ed25519PrivateKey.generate().private_bytes_raw())
path.chmod(0o600)
print(path)
PY
```

The issuer ID must begin with `dev-`. The receipt verifier still needs an independently trusted copy
of the corresponding public key; a development signature proves integrity, not production trust.

## Real integration command

Prerequisites:

1. `openshell==0.0.59` installed and selected.
2. A reachable OpenShell gateway backed by Docker.
3. Docker server access for immutable image and cleanup evidence.
4. The external development key above.

```sh
python scripts/run_sandbox_case.py \
  --private-key "$HOME/.config/agent-skills-arena/dev-sandbox-ed25519.key" \
  --issuer-key-id dev-local-sandbox-01 \
  --benchmark-suite-digest sha256:<64-hex> \
  --skill-artifact-digest sha256:<64-hex> \
  --output-dir /tmp/arena-sandbox-evidence/run-001
```

The committed smoke case is deterministic and only proves the walking skeleton. It does not qualify
a skill. The real integration receipt must be admitted by `verify_sandbox_case_receipts`, the bundle
must survive tamper controls, and two runs must show distinct workspace nonces and no residue before
issue #3 can close.

## Exit codes

| Code | Meaning |
|---:|---|
| 64 | profile/case/policy/runner configuration invalid |
| 65 | development signing key invalid or unsafe |
| 66 | output directory already exists |
| 70 | OpenShell binary, version, or gateway unavailable |
| 71 | Docker substrate unavailable |
| 72 | sandbox create/trailing command failed |
| 73 | result transport failed |
| 74 | image, policy, result, or attestation evidence incomplete |
| 75 | sandbox or backing container deletion not proven |
| 76 | physical run window exceeded the preregistered wall limit |
| 77 | deterministic task result differed from preregistered evidence |

## Test boundary

`tests/test_sandbox_executor.py` uses two layers:

- a deterministic driver to prove receipt compatibility with the existing Arena verifier;
- a scripted OpenShell/Docker command boundary to prove command sequencing, evidence collection,
  cleanup-on-transport-loss, and delete-failure behavior.

Those tests are necessary but not equivalent to a real OpenShell run. The repository status record
remains `real_integration: not_executed` until physical evidence is produced.
