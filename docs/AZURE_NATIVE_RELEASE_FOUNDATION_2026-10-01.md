# Azure-native release foundation — 1 October 2026

## Decision and boundaries

Owner chose Azure Container Registry Tasks and Azure-controlled approvals rather
than Azure DevOps or GitHub. This is the first foundation stage, NOT a completed
CI/CD service. No Azure DevOps terms were accepted. No Git commits or resets were
made; the existing dirty working tree is preserved.

## Deployed

Incremental Bicep deployment `release-archive-foundation-20261001` succeeded.
Its reviewed what-if showed only two new private containers in the existing
`ibkrv2stgauhecwc4itaoo` account:

- `release-sources`: source-only archives.
- `release-evidence`: unapproved source records and read-back receipts.

Anonymous blob access is disabled at the account and each container is private.
Existing blob versioning and 30-day blob/container soft deletion were verified;
no account-wide retention or networking settings were changed. The endpoint
remains network-accessible with authentication; this is not a private endpoint.
No WORM policy was locked and no object is claimed deletion-proof.

## Implemented and locally tested

- `source_archive.py`: explicit source-path/type allowlist; rejects traversal,
  linked paths, oversized/non-text files and selected credential patterns.
  No blanket test-file exemption: only the reviewed fake token literal and
  secure Bicep parameter expressions are exempted.
- Deterministic ZIP with per-file hashes and inventory hash; checks the entire
  archive without extracting or executing it. Source archives never convey
  deployment approval.
- `prepare_source_archive.py`: records current source, including new code, and
  honestly records the dirty worktree rather than pretending HEAD describes it.
- `publish_source_archive.py`: Entra/CLI identity only; pinned subscription and
  tenant; private-container check; create-only uploads; version-pinned download
  verification; no overwrite, SAS, storage key, approval or deployment operation.
- Infrastructure source: `infra/release-archive-foundation.bicep`.

Full local suite: **433 tests passed** (including seven new archive tests).
Limited repository policy scan: **335 source files**, **53 contiguous migrations**.
These checks are not a full independent secret or vulnerability scan.

Prepared archive: **315 source files**, **1,001,214 bytes**.
SHA-256: `ff2f66169a47d821d4bd0fc68b92453cfdb75dfdae2fdb675e8522b6e958a386`.
Local directory: `.work/azure-native-release/` followed by that digest.
It excludes operational documents, statements, environment files, report PDFs/
PPTX files, private templates, renderer binaries and Git history. It still
contains application constants/migrations and must remain private company code.

## Upload permission boundary — approved container-scoped access

Azure rejected the first blob existence check before upload: the signed-in
`anthony.ying@wealthryse.ai` has resource-management authority but no applicable
blob data access to the new containers. That initial attempt uploaded nothing.

The owner subsequently explicitly approved **Storage Blob Data Contributor**
for that user at ONLY the two new container scopes. Both assignments were
created and read back for principal `8617fd1c-2280-40f2-92dd-45686e9d686c`:

- `release-sources`: assignment `44ecea48-4f8d-40a4-bde7-828a051ed0d1`.
- `release-evidence`: assignment `423448cf-6101-4596-b7dc-ed86abfe5e5a`.

No storage-account or resource-group role was added. Initial propagation delay
was resolved by a later read-only check; no key or SAS fallback was used. This role
also allows deletion within those two containers; versioning/soft deletion are
recovery aids, not a guarantee against a privileged actor. Future automated
publishers need their own restricted identity, not the owner's login.

The prepared 315-file archive was then uploaded with its source record and a
verification receipt. All three pinned blob versions were downloaded and their
SHA-256 checksums matched. The record remains `archived_not_approved`; neither
upload nor this permission approval authorizes production deployment.
Initial receipt version: `2026-10-01T17:29:52.1704623Z`.
Local evidence: `.work/azure-native-release/` + the source digest above +
`/archive-receipt.json`. Verification receipts also reside in `release-evidence`
under the same source digest and their own content-addressed names.

Safe-rerun test passed: a second publisher execution preserved source version
`2026-10-01T17:29:16.3674663Z` and source-record version
`2026-10-01T17:29:36.5592898Z`. Only a new verification receipt was added
(`2026-10-01T17:31:03.3157202Z`); no source or source record was overwritten.

Existing storage-account-scoped service principals include broad data readers
and writers. Therefore these containers alone are NOT an isolated approval
trust boundary. Before a production release controller is enabled, use a
separately protected approval/evidence store and identities (or a reviewed RBAC
scope reduction). Do not silently reduce permissions on current production jobs.

## Remaining implementation gates


`scripts/node_modules` resolves through an existing junction into the desktop
runtime. Its configured location is not a self-contained, version-controlled
build dependency. Read-only inventory explicitly resolved that one known root;
no copy, patch, upload or execution of the private runtime was performed here.

Added `build_inputs.py` and `inspect_release_inputs.py` to fingerprint the seven
private template/intake files, 208 runtime files (66,688,613 bytes).

Local evidence:
`.work/azure-native-release/input-inventories/544888f715dd6444df75cd96b218660ec82f71a94a469bf30ff4baa34bb21b52.json`.
That filename is the SHA-256 of the inventory bytes.

Added tests for byte-level template/runtime correction detection, path rejection,
restricted/unknown licensing and rejection of caller-supplied approval flags.
The local release-evidence policy now requires `build_input_integrity`,
`production_rights_review`, and `build_inputs_manifest_sha256` evidence. As
previously documented, this policy is a local foundation; no Azure release
controller is connected to it yet. This is not a claim of deployed enforcement.

Local suite: **438 passed**. Limited repository policy scan: **339 source files**,
**53 migrations**. Independent secret/OS/Node vulnerability scans, an SBOM and a
candidate-image smoke test have NOT run in this continuation. Local Docker has
no running daemon, and the existing virtual environment has no `pip` module;
neither condition is reported as a successful dependency/security check.

No ACR task/build, production deployment, live Flex request, new access grant,
report regeneration or template alteration occurred. The previously uploaded
315-file source archive remains the previous baseline, not these new changes.


1. Uploader access, version-pinned upload/read-back and safe-rerun verification
   are complete. No archive is a deployable or approved release by itself.
2. Securely provision separately hashed private templates and renderer runtime.
   The current Dockerfile requires these excluded inputs; source ZIP alone is
   intentionally not build-ready. Verify runtime licensing/provisioning rights.
3. Add ACR tasks for actual build, independent secret/dependency/OS scans, SBOMs,
   image smoke tests and isolated staging acceptance. No ACR task was created
   or triggered in this foundation stage.
4. Bind evidence to exact source archive, build inputs, image digest and target
   configuration. The 2 October source change to `release_evidence.py` adds
   byte-verified archive provenance (see below); the trusted controller still
   needs to verify actual gate evidence, build attestation and target
   configuration. Neither fake commits nor `approved=true` are approval.
5. Add authenticated owner-only approval UI and authoritative server-side
   audit records, expiry/revocation, replay prevention and release-specific
   approval. Ordinary source upload/build identities must not grant approval.
6. Separate restricted deployment identity; verify protected approval and all
   evidence, deploy only the approved image/targets, check health, and register
   a verified rollback image. Database migrations require separate recovery.

## Production safety confirmation

No production image, identity, formula, report approval or schedule was changed.
Live `ibkr-v11-daily` remains cron `0 8 * * *` (**16:00 HKT**) with retry limit **0**.
No live Flex requests were made. Alpha World MS onboarding remains paused.

## 2 October continuation: archive provenance validation

`validate_release_evidence` now supports the Azure source-archive route without
claiming that an uncommitted tree is a clean Git revision. Existing clean-Git
validation remains supported and is not weakened. Archive mode requires:

- An explicit `source` mapping with exactly `kind: archive`, `archive_sha256`
  and `tree_sha256`; mixed Git/archive declarations are rejected.
- The actual frozen ZIP bytes, passed as `source_archive_bytes`. The existing
  archive verifier checks size/path/content restrictions, the complete per-file
  inventory, archive SHA and tree SHA without extraction or execution.
- Every required gate bound to the same immutable image, archive, tree,
  build-input manifest, template manifest and migration manifest.
- All existing scans, runtime and staging gates, artifact hashes and a distinct
  verified rollback image. No gate was dropped, including the existing
  production-rights review record.

This is a **source-level structural/integrity validator**, not a deployed release
controller or an approval mechanism. It does not authenticate evidence authors,
download and validate each gate artifact, prove an ACR build consumed the archive,
or confer permission to deploy. Trusted evidence verification, protected owner
approval with expiry/revocation/replay protection, target-configuration binding
and restricted deployment authority remain required. Existing deployment helpers
have not been relabelled as gated pipelines.

Ten new tests cover missing bytes, tampering, invalid ZIPs, ambiguous provenance,
cross-source/build-input evidence, gate failures and malformed input; **708 tests
passed** in the full suite. The limited policy scan passed for 430 source files
and 55 source migrations. Neither result is a vulnerability certification.

Current-source archive: 407 files, 1,192,731 bytes. SHA-256:
`ec35d9a1e8784b1fd50f9d956551c49dfcc09dcf466f2679dd40ceb56c8bec6a`.
Tree SHA-256:
`10805be595bb0f5ce6154df1f431737c6b6dd2616d8599a9be056a9f3e3b91b1`.
The record preserves `git_worktree_dirty: true` and `build_ready: false`.
Create-only upload and independent version-pinned readback of source, record
and receipt succeeded in the existing private release containers. Receipt SHA:
`8216b94525fb06cd5d3bc1a04347df15fbe98e23c6b0977a2328d74a6af9e33a`,
version `2026-10-02T01:22:13.8433289Z`. Local receipt is under
`.work/azure-native-release/<source SHA>/archive-receipt.json`.
State remains **archived_not_approved**; no image build or production deployment
was performed, and no new role was granted.

## 2 October continuation: release-package bytes and trusted-input boundary

Added `release_package.py` on top of the source-provenance validator. It verifies
actual gate envelopes, referenced scan/test output bytes, SBOM and all manifest
artifacts. Each gate binds the entire candidate except the gate-record map (to
avoid a circular hash). The binding includes source, immutable image, rollback,
build inputs, templates, migrations and target inventory. A hash-correct envelope
with a failed result cannot inherit a manifest's claimed pass.

Trusted adapter inputs are mandatory: artifact-digest-to-producer attestations,
server-owned per-gate producer allowlists, fresh ARM-derived target bytes and
timezone-aware server time. Uploaded producer claims alone fail. Evidence must
be current, not future-dated, and have a validity window of at most 24 hours.
Target changes fail closed. JSON duplicate keys/non-finite values, missing or
extra artifacts and excessive sizes/counts fail validation. The verifier never
extracts source, executes artifacts, fetches URLs or performs Azure writes.

**This is not yet an authenticated Azure collector or protected approval
service.** Hash equality does not prove authorship or scan adequacy; the future
adapter must authenticate the attesting producer and validate its scan/build
semantics. Do not populate trusted inputs from browser fields, ordinary blob
metadata, or a self-asserted producer ID. No scanner pass record was manufactured.
The returned receipt explicitly grants neither approval nor deployment authority.

Tests use fixtures only: 16 new tests; full suite **724 passed**; limited policy
check 432 files/55 source migrations. Production configuration is unchanged.
Current prepared source archive SHA:
`250917b08ce79a0ea3eb9ac1e39624cca3cf2b54937344923a6a48816443c182`
(409 files, 1,198,337 bytes). It supersedes the preceding source backup for this
component, not the production release. The existing maintenance action broker
was inspected and deliberately not extended to authorize deployments.

The new archive, record and receipt were uploaded create-only to the previously
approved private containers and independently read back at pinned versions.
Receipt SHA `7df43e24ccddb917dfad8279cc46fd9ee9d52a9466c5005758e667ebf9bcd4ad`,
version `2026-10-02T01:36:11.5929498Z`. State is `archived_not_approved`;
the previous backup was not overwritten and no new permissions were granted.
