# Alpha World MS HKD / 1M HIBOR Azure cutover

## Authority and scope

The owner explicitly requested replacement of the Azure calculation version by
the reviewed HKD version. The owner confirmed HIBOR usage rights for this system
and factsheets, and selected the arithmetic average of actual published 1M
HIBOR fixings from inception through the factsheet valuation date. Interest is
already reflected in the source NAV; no additional financing charge is applied.
The approving Azure identity matches the previous approved contract's identity
hash. This is not permission to alter IBKR source records, publish reports,
activate onboarding, or invent a HIBOR+Spread comparison convention.

## Qualified candidate

Exact image:
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:06c44ff756bbc7da0f41f14c9bfe08fff2ecb16e06a94c8576654bf176a00038`.

Native ACR build `cka3` passed 159 Linux tests, report-runtime smoke, and the
historical/future reporting, derived, risk and risk-free entrypoint imports.
The exact-image scan has zero critical, high and unknown findings. Its 40
medium and 19 low findings are retained, not dismissed. Source files and
licensed inputs are frozen and individually hashed in
`.work/alpha-ms-hkd-cutover-v14r11-20261007-r3/context/ALPHA_MS_HKD_RELEASE.json`.

Earlier failed build/probe attempts are retained separately. The final
historical financial inputs differ from the original review only in the already
qualified calendar-version and official-provider description fields. Every
source value, fixing and historical financial metric remains equal.

## Qualification evidence

- Read-only live preflight: `ibkrv2stg-onboard-render-63odtio`. The application
  role is not a superuser or RLS-bypass role, and temp-table permission exists.
- PostgreSQL interpreter/future collector: `ibkrv2stg-onboard-render-5hjz000`.
  All 443 historical HKD NAV observations match the independent review. Cache
  replay is idempotent; tenant isolation and persistent fingerprints pass.
- Archived-input Azure batch: `ibkrv2stg-onboard-render-a6mbfdp`. All 21 months,
  January 2025 through September 2026, render with the approved typography and
  allocation geometry. All 42 pages match the accepted HKD review pixels at
  1000-pixel resolution, with independent font/baseline and geometry checks.
- Atomic/versioned cutover and rollback rehearsal:
  `ibkrv2stg-onboard-render-4suw1t3`. The exact installed application code
  creates version 2 of the contract, updates only MS currency metadata and
  recalculable analytics, validates 419 licensed fixings through 7 October,
  builds 445 eligible historical risk caches, and creates 21 new frozen HKD
  reporting snapshots. The prior state is restored and compared by actual
  values. No persistent database writes occur in this rehearsal.

Both PostgreSQL rehearsals use session-local shadow tables in the existing
server, not an independent restored server or a disaster-recovery exercise.
Application commits are deferred to an outer transaction; source tables are
excluded from the mutation allowlist. Rollback covers metadata, contracts,
derived NAV/components/cash/positions, risk caches, HIBOR and snapshot status.
Original contract configurations and frozen reporting payloads are preserved.

## Coordinated installation and verification

The installation controller scopes writes to five existing job image digests,
the existing website image/revision, and the risk-free job's
`HIBOR_LICENSE_SCOPE_CONFIRMED=true` flag. Other identities, schedules, credentials,
resource settings and retry policies must match their protected baselines.

The database cutover creates and verifies a protected, full MS rollback backup
before mutation, uses a serializable transaction and processing advisory locks,
and validates source/report/operating-registration fingerprints before commit.
Legacy USD NAV rows are retained as historical rollback evidence; HKD is the
new current subject/reporting/calculation currency, not a second selectable
current methodology. New report versions replace old frozen versions without
overwriting their payloads or any published files.

The final installed-identity test reads actual persistent HKD snapshots and
risk caches, then renders all 21 PDFs through the installed renderer. It does
not substitute archived review JSON, fabricate future observations, publish
reports, or activate onboarding. Final success is recorded only after this test,
full-page acceptance and all six installed resource identities pass.

Completion receipt: `.work/alpha-ms-hkd-cutover-v14r11-20261007-r3/completion.json`.
Its absence means the coordinated replacement is not yet fully verified.

## Operations and rollback

Run `scripts/deploy_alpha_ms_hkd_cutover.py` stages individually. Intent records
are create-only. After an unknown outcome, inspect the original Azure execution
and state; never rerun a mutating stage or replace its evidence.

The previous five job/website image references and full non-image settings are
in `coordinated-baselines.json`. The transaction's full database rollback backup
and its SHA-256 are referenced by the protected `database-cutover-<image>.json`
receipt. Restore the database with the rehearsed scoped restoration logic and
then restore the recorded runtime references; reverting an image alone is not
a financial-methodology rollback. Reject rollback if any new snapshot has
subsequently been released or if another operator has changed the resources.

HIBOR+Spread is still the approved factsheet heading, but benchmark comparison,
composite crisis replay and security-level tail attribution remain explicitly
unqualified. The calculation switch does not pass their acceptance checks.
The existing MS onboarding/registration/publication controls remain effective;
future automatic issuance requires those controls and validated inputs. Neither
the cancelled agentic programme nor the paused Bloomberg integration is resumed.
