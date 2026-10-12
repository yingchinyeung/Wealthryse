# Alpha World MS: authenticated website acceptance

Current status: website image alignment to qualified v14r13 is complete and
verified. Registered historical report delivery is NOT repaired yet; the old
batch remains blocked. Do not approve that batch or advance activation.

## Latest scoped website alignment - v14r13

- Owner explicitly agreed to continue the proposed image-only website update
  after confirming that v14r13 includes the newest distribution-axis correction.
- Frozen v14r11 and qualified v14r13 website source files are byte-identical.
- Isolated execution `ibkrv2stg-onboard-render-jbivfhj` passed six checks against
  live read-only HKD data: health, anonymous denial, unregistered-user denial,
  owner preview, historical page access, and legacy batch approval remaining
  blocked. The ASGI test does not itself exercise the Entra gateway.
- Updated only `ibkrv2stg-web` to digest
  `sha256:f7aaa6935794c7a2ce1ef94ed177fa3f987fd31df8df7d6b56f644242922a590`.
- Ready revision `ibkrv2stg-web--ms-layout-v14r13` is active, Healthy and Running,
  with 100 percent traffic and successful provisioning.
- The first immediate post-command comparison failed. No
  update was repeated. Read-only reconciliation of the completed original
  operation found no governed differences, and final verification passed.
- Authentication properties, environment, identity, credential references,
  scaling, ingress and every other governed non-image setting match baseline.
- Refreshed the owner's existing Chrome historical-reports page after cutover;
  the owner identity and authenticated access still work. This is not a fresh
  login test. The page still shows legacy manifest prefix `8bbbb06368586f73`,
  stale template evidence, failed artifact validation and regeneration-only
  action. No regeneration or approval control was clicked.
- Intent, staging evidence, original-operation reconciliation and protected
  completion receipt are under `.work/alpha-ms-web-v14r13-20261007/`.
- Thirteen local safeguard/immutable-batch tests and seven parameter subtests
  passed; these supplement, not replace, the live Azure and browser checks.
- No database write, IBKR source change, report approval, publication or MS
  activation occurred. The existing historical batch has not been replaced.

Next: qualify an MS-specific HKD/HIBOR presentation path without inventing
benchmark, crisis or attribution results; then qualify and deploy the private
worker changes, regenerate a new paired 21-month batch, and check authenticated
downloads. The already accepted private Azure v14r13 factsheets must not be
confused with the old registered website artifacts.

The sections below preserve the earlier checks and pre-alignment history.

## Read-only browser check

- Used the owner's existing Chrome session, refreshed the historical-reports
  page and confirmed the displayed identity `anthony.ying@wealthryse.ai`, Owner.
- Page: `/account-onboarding/alpha-world-ms/steps/historical-reports`.
- Website lists 21 months / 42 files, awaiting owner approval, with last server
  update 1 October 2026 21:05:39 HKT and manifest prefix `8bbbb06368586f73`.
- Current checks report stale template evidence and failed artifact validation;
  approval is blocked. The normal UI offers regeneration, not approval.
- Downloaded the exact September factsheet via the authenticated website link
  `/api/onboarding/alpha-world-ms/historical-reports/834/download`.
- The download completed as
  `C:/Users/antho/Downloads/alpha-world-ms_Factsheet_2026-09-30 (1).pdf`.
- Download SHA-256:
  `a0b1c628c668075cc6bdd3093329bd5c213a3580882eecc3f7d12215aa83141c`.

## Content acceptance failed

Inspected searchable text, PDF metadata, and both rendered pages using Poppler.
The website download is a legacy PDF, with USD base currency and capital-path
labels, a 4.20% risk-free rate, initial capital 100 instead of 100,000, old chart
formatting and disclaimer spacing. It lacks the current MS calculation and copy
version metadata. It is not the accepted v14r12 cloud-generated factsheet.

The accepted September v14r12 PDF has SHA-256
`0388aa49dc1d49b8ac4d43fc4432085ca741af0f5e025280a4851f78bcdb2773`,
calculation version `alpha-world-ms-factsheet-hkd-hibor-v3`, HKD currency and
inception-to-valuation published 1M HIBOR mean. Its risk-free annual rate is
`0.02646383662650602`; copy version is
`alpha-world-ms-disclaimer-spacing-v14r12`.

The newer accepted files were generated privately in Azure. That did not replace
the existing immutable onboarding batch linked by the website. Renderer
installation and private cloud acceptance must not be confused with registered
website delivery or publication.

## Next scoped work

Qualify and regenerate a new registered private historical batch using the
installed renderer and current HKD frozen snapshots. Validate both report types
and all 21 months, bind the new manifest to current contracts, then repeat the
authenticated download/content checks before requesting owner approval.
Preserve the old batch as historical evidence. Do not overwrite protected source
files, approve reports, activate MS, or bypass any gates.

No regeneration, database change, approval, publication or activation was
performed during this check. No IBKR data was changed. No new sign-in was needed;
this checks existing authenticated-session access, not a fresh login flow.

## Owner-authorized regeneration follow-up

The owner authorized Azure account use and private batch regeneration. Safe
read-only qualification ran in the existing reporting job identity, with no
new resource, role, credential, persistent configuration or database changes.
Evidence is retained under `.work/alpha-ms-website-batch-20261007/` and the
private `review/alpha-world-ms/website-batch-20261007/` Azure prefix.

- First diagnostic `ibkrv2stg-onboard-render-rp96sbq` failed on a null optional
  analytics field. Its original intent/dispatch are preserved, not relabelled.
- Corrected diagnostic `ibkrv2stg-onboard-render-gwnt8qp` succeeded. All 21
  distinct active HKD frozen snapshots passed integrity, currency and private
  publication-boundary checks, and their metrics were recomputed read-only.
- Current subject lifecycle remains onboarding. Twelve historical batch records
  exist; no batch was superseded or queued by this follow-up.
- Azure synthesizes the execution-only DATABASE_URL secret reference as
  `cappjob-ibkrv2stg-onboard-render`. Nonsecret execution inputs match exactly,
  and the persistent job invariant, including credentials, is unchanged.
- The presentation content-expectation function accepts the snapshots, but this
  is not full presentation runtime acceptance. The actual Node renderer
  `scripts/render_monthly_presentation.mjs` rejects non-USD reporting, assumes
  benchmark comparison and crisis inputs, and prints UST risk-free descriptions.
  The HKD snapshots deliberately do not claim those unqualified capabilities.
  Relabelling the existing USD slides would not be a valid correction.

Prepared, NOT DEPLOYED: content-addressed create-only review artifacts with
byte-for-byte readback, plus an MS-only path to reuse current validated frozen
snapshots without refreshing unqualified crisis results. Fifteen targeted tests
passed. Full presentation qualification and release qualification remain required
before these worker drafts can be installed.

At this earlier check, the live website was still on v14r11 while both factsheet reporting jobs were on
v14r12. A proposed image-only website alignment to the already-scanned v14r12
image was blocked by the deployment safety review as a separate live website
cutover requiring explicit approval. The update command never ran; no website
alignment intent, image/configuration update, regeneration, report approval,
publication or activation occurred. Do not bypass or indirectly execute it.

The earlier authority request (now superseded by the approved v14r13 update): explicit approval to change only the `ibkrv2stg-web`
image to the installed v14r12 immutable image, preserving authentication,
credentials and all non-image settings. This may briefly affect availability.
After that, qualify the HKD presentation path before queuing a full paired batch.

## HKD presentation follow-up

The MS-only HKD/HIBOR presentation path has now passed local September acceptance:
all twenty rendered slides were inspected, all six native financial charts and
seven risk-table metrics passed exported-package checks, and thirty-one targeted
tests plus thirteen subtests passed. All twenty-one unchanged Azure frozen input
hashes passed the read-only rendering-model check. The native allocation-table
overflow and clipped cash caption were corrected without changing approved font
sizes; contribution magnitudes and signed labels are now explicit.

These renderer/worker changes remain NOT DEPLOYED. The website's historical batch
has not been regenerated or replaced. All-month exported-deck QA, registered
contract binding, scoped image/release/runtime/rollback qualification and current
owner identity revalidation remain before any new private batch registration.
Benchmark comparisons and simulated/crisis drawdowns remain explicitly unavailable.
See `ALPHA_WORLD_MS_PRESENTATION_QUALIFICATION_2026-10-07.md` for evidence and gates.
