# Agent Integration Requirements｜Qualification and Arena Plane

> Status: canonical human/agent handoff for `ed3c/agent-skills-repo`.
>
> 本文件定義本庫如何接收 `ed3c/tech-implementation-atlas` 的 immutable qualification handoff，產生可驗證的 physical execution evidence，並在獨立 admission 後回傳 lifecycle authority。它不允許從格式、local tests、PR prose 或 Arena score 推導 qualification。

## 0. Mandatory read order｜強制閱讀順序

任何 Agent 在修改 Skill assets、portable exports、sandbox executor、signing、admission、Arena evaluation 或 Atlas integration 前，必須依序讀取：

1. `INTEGRATION_REQUIREMENTS.md`
2. `AGENTS.md` 或 `CLAUDE.md`
3. `README.md`
4. `openwiki/quickstart.md`
5. `openwiki/qualification-pipeline.md`
6. `docs/sandbox-executor.md`
7. `docs/agent-skills-portability.md`
8. 受影響的 `contracts/`、`skill_arena/`、`scripts/`、`data/`、tests 與 generated registry
9. Atlas handoff schema、exact handoff artifact 與 requested host/profile/policy/task-pool inputs

若任一路徑、receipt、attestation、manifest、trust root、public key、cleanup proof、landing authority 或 test evidence 不存在於 declared ref，必須回報為 evidence gap。不得用預期路徑或協作文字補足 physical evidence。

## 1. Repository role｜本庫唯一責任

本庫是 Skill qualification、execution evidence、admission 與 Arena comparison 的獨立 authority plane：

```text
Atlas immutable handoff
  → schema and digest validation
  → pinned Skill / host / profile / policy / task-pool / threshold
  → isolated execution
  → assertion and security checks
  → cleanup/destruction proof
  → reproducible fresh-workspace run
  → signed qualification result
  → independent verification
  → reviewed lifecycle admission
  → Atlas authority refresh
```

本庫擁有：

- repository-native Skill sources and lifecycle records；
- portable Agent Skills exports and byte digests；
- sandbox executor contract and physical evidence landing；
- signing, verification, replay/tamper protection；
- qualification decisions and admission records；
- Arena paired comparison, no-Skill baseline, uncertainty, cost, latency, safety and compatibility metrics；
- repository-local landing evidence authority。

本庫不擁有：

- complete private note bodies or source transcripts；
- Atlas Domain/Capability intent inference；
- automatic Claim admission；
- production trust root unless explicitly materialized and reviewed；
- universal marketplace or single-score leaderboard；
- permission to treat Arena rank as qualification。

## 2. Authority separation｜必須分離的狀態

```text
portable format conformance
  != source anchoring

source anchoring
  != local correctness

local correctness
  != sandbox qualification

signed result
  != verified result

verified result
  != lifecycle admission

lifecycle admission
  != production routing

Arena comparison
  != qualification
```

每個狀態必須有獨立 schema、evidence、actor/authority、timestamp、digest 與 revocation path。

## 3. Atlas handoff input｜上游不可變輸入

Expected handoff must bind at minimum：

```text
schema version
handoff ID and nonce
Capability ID and version
Skill ID and canonical SKILL.md digest
assertion-contract digest
exact source claim set
host and host compatibility declaration
sandbox profile digest
policy digest
task-pool digest
thresholds
required reproducible run count
current Skill lifecycle / Evidence Grade / routability
target qualification repository
issued and expiry timestamps
```

Input validation must fail closed on：

- unknown or unsupported schema；
- missing field；
- digest mismatch；
- changed Skill/assertion/claim set；
- stale or expired handoff；
- duplicate/replayed nonce；
- unsupported host/profile/policy；
- undeclared network, secret, filesystem or production access；
- trust or license gate failure。

Qualification must execute the immutable handoff artifact. It must not silently rebuild a different Skill from prose or a moving branch.

## 4. Physical execution evidence｜實體執行證據

A qualification-eligible run must produce repository-verifiable evidence such as：

```text
execution receipt
attestation
artifact manifest
command/tool log
changed-path manifest
test/assertion report
security violations report
environment/image/profile/policy identity
source and Skill digests
start/end timestamps
cleanup/destruction proof
signer/key identity
result digest
```

The evidence must distinguish：

- contract tests run in CI；
- local developer run；
- real sandbox/gateway-backed run；
- fresh-workspace reproduction；
- production observation。

CI that only tests the executor contract must remain `qualification_eligible: false`.

## 5. Assertion and security gates｜斷言與安全 Gate

Mandatory checks include：

### Pre-execution

- immutable handoff and Skill digest；
- source/assertion/claims binding；
- host/profile/policy/task-pool support；
- secret absence in repository, inputs and environment declaration；
- network/filesystem/tool boundaries；
- no unauthorized production action。

### During execution

- command and tool policy；
- path allowlist；
- network deny-unless-declared；
- resource/time limits；
- secret redaction；
- assertion event capture；
- denominator-preserving failure recording。

### Post-execution

- mandatory assertions pass；
- `security_violations == 0`；
- artifact and result digest consistency；
- cleanup/destruction proof；
- required fresh-workspace repetitions；
- no hidden retries that change the preregistered experiment；
- signed result and evidence manifest consistency。

Any mandatory failure must block qualification result publication or produce an explicit failed/non-eligible result, never a partial success claim.

## 6. Signed qualification result｜簽章結果

A signed result must bind：

```text
result schema/version
handoff digest and nonce
Capability/Skill/version IDs
Skill/assertion/claim/host/profile/policy/task/environment digests
execution timestamps
assertion summary
security violations
cleanup proof
reproducible run count
qualification decision and reason
issuer repository/executor scope
key ID and signature
issued/expiry timestamps
result digest
```

Signing private keys must come from an external secret source or controlled runtime environment. They must never be committed, embedded in fixtures, logged, uploaded as artifacts, or copied into result files.

## 7. Verification and trust roots｜驗證與信任根

A downstream verifier must check：

- Ed25519 signature；
- active, non-revoked public key；
- key validity interval；
- issuer repository and executor scope；
- result timestamp ordering and expiry；
- exact handoff digest；
- exact Skill/assertion/claims/host/profile/policy/task/environment digests；
- mandatory assertion status；
- zero security violations；
- cleanup proof；
- reproduction requirement；
- nonce replay and result tamper；
- stored verification digest。

A repository with an empty or unreviewed production trust-root set must fail closed for production admission.

## 8. Lifecycle admission｜生命週期授權

Verification success does not mutate Skill lifecycle automatically. Admission requires an independent reviewed authority record with：

```text
exact verified result digest
exact immutable Skill identity
approved lifecycle transition
reviewer/policy authority
approval reason
issued/expiry timestamps
revocation/supersession path
production-routing permission where applicable
implicit-invocation permission where applicable
```

Production routing requires explicit flags and a production-scoped trust/admission policy. Sandbox qualification alone must not enable production or implicit routing.

Authority becomes stale or invalid when any of these change：

- Skill bytes or version；
- assertion contract；
- source claim set/version/freshness；
- host compatibility；
- sandbox profile/policy/environment；
- task pool/threshold；
- security advisory；
- trust root or signer status；
- revocation/supersession record。

## 9. Portable Skill export boundary｜可攜 Skill

Canonical external Skill exports must preserve：

- valid Agent Skills frontmatter；
- behavior content and resource paths；
- lifecycle metadata；
- evidence/routability state；
- deterministic digest；
- source-to-export loss accounting。

Format conformance can prove portability only. It must never change lifecycle or qualification state.

Generated exports must be derived from canonical repository-native sources. Direct edits to `dist/` without corresponding canonical-source changes must fail validation.

## 10. Arena boundary｜比較評測

Arena evaluation compares baseline and candidate under the same preregistered envelope：

```text
same task and pairing key
same agent/model/tool/policy/image identity
same retry/concurrency rules
explicit no-Skill baseline
candidate exposes only declared Skill set
raw outcome preservation
cost/latency/reliability/safety/compatibility metrics
uncertainty and paired analysis
```

Arena must preserve failures in the denominator and reject leakage from public/blind pools. A successful positive control or one-task matrix is not a public leaderboard claim.

Arena result may inform ranking and research. It cannot silently grant qualification, production routing, or lifecycle admission.

## 11. Atlas round-trip｜回傳 Atlas

The integration output to Atlas consists of：

1. signed qualification result；
2. independently verified record；
3. optional reviewed lifecycle admission authority；
4. revocation/staleness signals；
5. exact immutable digests needed by Atlas hard gates。

Atlas must be able to determine independently：

```text
qualified?
verified by trusted root?
admitted to which lifecycle?
production routing allowed?
implicit invocation allowed?
valid for which host/profile/policy/task envelope?
stale/revoked/superseded?
```

The round-trip must not require Atlas to trust a PR body, Issue state, README status or local transcript.

## 12. Repository-local delivery evidence｜落地證據

An implementation item is delivered only when the repository-local authority binds：

- full commit reachable from the required branch；
- exact changed paths derived from Git history；
- digest of those paths；
- digested test/receipt evidence；
- independent verification where required。

Issue checkboxes、comments、Projects cards、short commit IDs and PR prose are coordination views, not delivery authority.

## 13. Agent execution protocol｜Agent 執行步驟

1. Read mandatory files and identify current Skill lifecycle/evidence state.
2. Resolve exact Atlas handoff and immutable digests.
3. Validate schema, scope, host/profile/policy/task and freshness.
4. Build or select the pinned execution environment without changing the declared envelope.
5. Execute with fail-closed assertion/security/cleanup behavior.
6. Preserve all successes and failures in physical evidence.
7. Run required fresh-workspace reproduction.
8. Sign only after all eligibility conditions pass.
9. Independently verify signature, digests, replay protection and evidence.
10. Create lifecycle admission only through the declared review authority.
11. Update exports/registries/landing evidence deterministically.
12. Report implementation, local tests, physical execution, signing, verification, admission, merge and production routing as separate states.

## 14. Definition of Done｜完成條件

A qualification/integration change is complete only when：

- canonical source and schemas exist on the declared ref；
- unit/contract/security/tamper/replay tests pass；
- generated exports are deterministic and current；
- exact Atlas handoff is validated；
- real physical execution evidence exists when qualification is claimed；
- required reproduction and cleanup proof exist；
- private signing key is absent from repository history and artifacts；
- signed result verifies against an active scoped trust root；
- lifecycle admission is separate and reviewed；
- repository-local landing evidence binds full commit, paths and evidence；
- GitHub read-back succeeds；
- incomplete physical integration remains explicitly non-eligible；
- final report lists blocked and not-attempted work without overstating status。

## 15. Forbidden shortcuts｜禁止事項

Agent不得：

- 把 format validation 或 CI contract test 當 sandbox qualification；
- 從 Atlas prose 或 README 重建不同於 handoff digest 的 Skill；
- commit、log 或 artifact-upload signing private key；
- 在 cleanup/destruction 未證明時發布 eligible result；
- 忽略 failed runs 或從 denominator 移除 provider/tool failures；
- 以 Arena rank 改變 qualification；
- 以 signed result 自動建立 admission；
- 以 sandbox admission 自動開啟 production/implicit routing；
- 用 Issue、PR、expected path、local transcript 代替 repository-local evidence；
- 宣告 real OpenShell/gateway integration，除非 exact physical bundle 已落地並通過 authority gate。

## 16. Validation entrypoints｜驗證入口

依受影響範圍執行 repository 中存在的檢查，至少包含：

```bash
python3 scripts/git_gate.py
python3 scripts/check_landing_evidence.py --main-ref origin/main
python3 scripts/export_agent_skills.py --check
python3 -m pytest -q tests/test_agent_skills_export.py
python3 -m pytest -q tests/test_sandbox_executor.py
```

若修改 qualification/Atlas bridge，還需執行：

```text
handoff schema tests
signature success/tamper/revocation/expiry tests
nonce replay tests
digest mismatch tests
scope tests
security violation tests
cleanup proof tests
fresh-workspace reproduction tests
round-trip Atlas verification tests
```

不存在或尚未 materialize 的 command/test 必須回報為 gap，不得偽造 PASS。

## 17. Completion report template｜回報格式

```markdown
## Scope
- Skill/Capability:
- host/profile/policy/task pool:
- requested lifecycle:

## Immutable inputs
- handoff:
- Skill/assertion/claim digests:
- environment digests:

## Execution evidence
- physical run:
- assertions:
- security violations:
- cleanup proof:
- reproducible runs:

## Signature and authority
- signer/key scope:
- verification:
- lifecycle admission:
- production/implicit routing:

## Delivery
- commit/paths:
- landing evidence:
- tests:

## Status
- implemented:
- tested locally:
- physically executed:
- signed:
- verified:
- admitted:
- merged:
- production-routable:
- blocked/not attempted:
```
