# OpenShell physical evidence admission

This document defines the second half of issue #3. The sandbox executor contract
can be reviewed and tested without a gateway; physical evidence cannot. A pair
becomes admissible only after two independent OpenShell 0.0.59/Docker runs have
produced signed bundles and the offline verifier has admitted both.

## Why two layers exist

`tests/test_sandbox_executor.py` proves the contract, signing envelope, error
states, and command sequencing with deterministic test doubles. It does not
prove that a live gateway created a sandbox, that a Docker backing container
existed, or that deletion removed both identities.

The physical layer therefore consumes only bundles produced by
`scripts/run_sandbox_case.py` with the concrete `OpenShell059Driver`. It does not
start a gateway or rewrite evidence during verification.

## Required runner

The manual workflow requires a self-hosted runner with all labels below:

```text
self-hosted
linux
x64
openshell-evidence
```

The runner must provide:

- Docker server access;
- `openshell --version` resolving exactly to `0.0.59`;
- a reachable OpenShell gateway using the Docker substrate;
- `OPENSHELL_DEV_KEY_PATH`, pointing to an owner-only Ed25519 development key
  outside the checkout;
- no production credentials in the sandbox environment.

The workflow does not accept the private key as an input or artifact. It derives
only the public key into the evidence directory.

## Manual workflow inputs

Dispatch `.github/workflows/openshell-physical-evidence.yml` with:

```text
benchmark_suite_digest = sha256:<64 lowercase hex>
skill_artifact_digest  = sha256:<64 lowercase hex>
issuer_key_id          = dev-<development trust identity>
```

The workflow executes exactly two attempts. It does not retry a failed run. Both
return codes and logs are retained, the artifact is uploaded with `if: always()`,
and the final job fails after upload when either attempt or pair admission fails.

The physical job can run only from a manual dispatch on `refs/heads/main`, behind
the `openshell-physical-evidence` environment, on a runner carrying the required
labels. Pull-request code cannot invoke the self-hosted gateway or key.

## Bundle contract

Each successful physical run must contain exactly:

```text
run-NNN/
├── receipt.json
├── attestation.json
├── result.json
└── bundle-manifest.json
```

Extra files, symlinks, stale file hashes, stale bundle digests, result bytes that
do not recompute to the preregistered task evidence, or attestation fields that
do not bind the pinned profile are rejected.

The attestation must include the complete concrete-driver control surface:

- OpenShell version probe digest;
- gateway status probe digest;
- Docker server version;
- runner, requested policy, runner input, create output, effective policy, and
  Docker inspect digests;
- resource-enforcement identifier;
- proof that secrets were scrubbed and provider auto-discovery disabled;
- backing container ID and observed Docker memory limit.

## Pair admission

Run both offline gates when two bundles already exist:

```sh
python scripts/audit_development_private_key.py \
  --repo-root . \
  --private-key "$OPENSHELL_DEV_KEY_PATH" \
  --output /evidence/private-key-audit.json \
  --refs-output /evidence/repository-refs.txt

python scripts/verify_openshell_evidence_pair.py \
  --bundle /evidence/run-001 \
  --bundle /evidence/run-002 \
  --public-key /evidence/dev-public.key \
  --private-key "$OPENSHELL_DEV_KEY_PATH" \
  --issuer-key-id dev-openshell-evidence-01 \
  --benchmark-suite-digest sha256:<64-hex> \
  --skill-artifact-digest sha256:<64-hex> \
  --repo-root . \
  --output /evidence/pair-index.json
```

The pair verifier requires:

- both signatures admitted by `verify_sandbox_case_receipts` at their immutable
  issuance time;
- the same pinned image, policy, Docker server version, profile, case, benchmark,
  skill artifact, and issuer identity;
- distinct sandbox names, workspace nonces, receipt IDs, and backing container
  IDs;
- identical deterministic command, exit, timeout, stdout, stderr, and output
  evidence;
- cleanup and workspace destruction proven for both runs;
- an in-memory tamper control rejected for each receipt without mutating either
  original bundle;
- the external private key absent from every reachable Git blob and from tracked,
  untracked, and ignored worktree files.

The separate all-object key audit expands that boundary. The workflow fetches all
branches, tags, and GitHub pull-request head refs, then scans:

- every reachable commit, tree, annotated tag, and blob object;
- the exact fetched ref-name/object-id snapshot;
- reflog text;
- tracked, untracked, and ignored worktree paths and regular files.

The audit covers source bytes, raw Ed25519, PKCS8 DER, normalized PKCS8 PEM,
lower/upper hex, standard/base64url, and padded/unpadded encodings. Diagnostics
name only the Git object type/id or path; they never print key material.

It emits two content-addressed artifacts:

```text
environment/private-key-audit.json
environment/repository-refs.txt
```

`private-key-audit.json` binds the repository HEAD, exact ref snapshot digest,
reflog digest, object-type counts, worktree count, representations checked, and
an audit digest. `repository-refs.txt` makes the branch/tag/pull-ref set directly
reviewable rather than relying on an unobservable `--all` claim.

## Reproducible index

`pair-index.json` contains no runner-local absolute bundle paths and no field
whose value changes with the current clock. When `--generated-at` is omitted,
the index uses the latest immutable receipt issuance time. Re-verifying the same
repository refs, bundles, and key therefore reproduces the same `pair_digest`.

The workflow additionally creates `artifact-manifest.json`, hashing every
non-secret artifact file—including the key audit and exact ref snapshot—before
GitHub upload. The GitHub artifact digest is a transport-level checksum; the
repository pair, key-audit, ref-snapshot, and artifact digests remain the
portable evidence identities.

## What still does not close issue #3

A green offline contract test, an uploaded artifact without pair admission, or a
successful terminal transcript is coordination information. Issue #3 closes
only after:

1. the contract PR is reachable from `main`;
2. a successful physical workflow artifact is independently reviewed;
3. all non-secret raw bundles, pair index, key audit, exact ref snapshot,
   environment metadata, tamper and cleanup controls, and artifact digest land
   through a reviewable PR;
4. `data/verification_runs/openshell_executor_status.json` changes through that
   PR;
5. the final merge commit and evidence digests enter
   `data/project/landing-evidence.json` and pass its full-history gate.
