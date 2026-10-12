# Alpha World MS disclaimer typography correction

Status: deployed and verified in Azure for both MS reporting paths; all 21 historical months regenerated. Report release and onboarding activation remain separate.

## Scope

Correct the owner's three September v14r11 annotations: page-one footer,
page-two Disclaimer body, and page-two footer. Missing word spaces include
`Thisdocument`, `providedfor`, `theWealthRyse`, `andshould`, `Itsdesign`,
`investmentdiscretion`, `purposesonly`, `security,financial`, `applicablelaws`,
`arestrictly`, `managerretains`, `circumstancesshould`, `applicableregulatory`,
`anyparticular`, `lossarising`, and `independentanalysis`. Restore `may be` in
the distribution-restrictions sentence. No substantive disclaimer changes.
The five existing list responsibilities now have consistent bullet typography.

## Implementation

- `alpha_world_ms_copy.py` removes the incorrect underlying text-show operators,
  rather than leaving duplicate typo-filled text behind a white rectangle.
- Reflow the same wording with the authority's embedded Georgia (8.999791 pt)
  and IBM Plex Sans (4.912386 pt) fonts. Preserve the existing disclaimer border,
  page labels, and confidentiality labels.
- Clear only the main disclaimer interior's rasterized old list dots, then draw
  five aligned vector bullets. All corrected copy remains searchable.
- Place disclosure text and vectors in a separate Form so allocation validators
  still inspect only the diagram's actual geometry. No financial checks weakened.
- The MS-only shared renderer calls this correction and validates its real PDF
  text. Cache/layout version advances to v14r12, with a separate copy hash/version.
  Other subjects, including Alpha World T, retain their existing renderer paths.
- No source PDF, approved template, source NAV/FX/HIBOR input, or IBKR file changes.

## Evidence

September original SHA256:
`3a0966e1ddbed351a421a31ea949ad03a7f7dbb78b9c2bf40031a3c823bd53f9`.

Corrected September SHA256:
`71555dc346ffbc757314a7a8a7e580dc605cd388e6739f05c02fc8647171f676`.

Evidence manifest: `output/pdf/AlphaWorld_MS_Text_Corrections_v14r12/manifest.json`.

- All original PDF metadata, including metrics/calculation evidence, exactly equal.
- Independent whitespace-free text comparison confirms the disclaimer wording
  and punctuation are unchanged in all three annotated areas.
- Independent font/glyph checks preserve font family and size; no text clipping.
- Both 1800-pixel page rasters are pixel-identical outside the correction regions.
- Both full pages and close-up crops were visually inspected.
- 36 targeted tests passed, including stale chart/caption/geometry rejection and
  Alpha World T/account renderer regression checks.
- All 21 pinned January 2025-September 2026 HKD source inputs are qualified locally
  through the actual shared renderer, including existing financial/vector checks;
  inputs and approved template hashes are preserved. These test PDFs are scratch
  artifacts, not cloud reports or releases.

## Original review's remaining delivery step

Build, scan and qualify a fresh Azure runtime containing v14r12, then install it
and rerender the historical factsheets through the Azure reporting workflow.
The v14r11 runtime and frozen cloud reports were not changed by the original
text-review request. Its local outputs must not be relabeled as Azure-rendered.

## Azure deployment completed at the owner's follow-up request

Controller: `scripts/deploy_alpha_ms_copy.py`.
Protected completion receipt: `.work/alpha-ms-copy-v14r12-20261007/completion.json`.

- Native ACR build `cka4`; 163 Linux regression tests passed. The original build
  acknowledgement was reconciled, not redispatched.
- Installed image: `ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:f94f3a57b8170aeb294ec7dc69215738ab6c96a834d8019db78f1ebfaaaff827`.
- Exact image/SBOM scan passed: zero critical, high, or unknown findings;
  40 medium and 19 low findings remain in the tracked scan evidence.
- Candidate live-data execution: `ibkrv2stg-month-end-factsheet-8f2ol1t`.
- Read-only rollback execution: `ibkrv2stg-month-end-factsheet-qck0fsm`.
- Installed live-data execution: `ibkrv2stg-month-end-factsheet-2p9bydv`.
- All three runs passed against the actual persistent HKD frozen snapshots and
  risk caches. No reconstructed/local payload substitution or database writes.
- All 21 candidate PDFs passed independent real-text/font checks, identical
  financial metadata, and pixel comparison of all 42 pages outside the three
  corrected regions. Seven contact sheets plus September full pages inspected.
- Both installed reporting jobs use v14r12: `ibkrv2stg-onboard-render` and
  `ibkrv2stg-month-end-factsheet`. Their full non-image settings, including identity,
  credentials, scheduling, commands, resources, timeout/retry policy, are unchanged.
- Every installed PDF is byte-identical to its accepted candidate PDF.
- The legacy `historical-reports` job queues/freezes snapshots, not PDF rendering;
  `report-snapshot` likewise does not render PDFs. No additional MS renderer was
  found in the resource group's reporting job inventory. Website and other jobs
  are untouched by this typography-only release.
- Both historical and future entrypoints use the corrected shared renderer and
  new render-contract/layout version, preventing reuse of old-format draft cache.
  Existing validated-input, registration, activation and approval gates remain.
  Alpha World MS is still in onboarding; this is not authorization to release it.
- No IBKR file, NAV/FX/HIBOR input, calculation module, or template asset changed.
  Old cloud/local output versions remain intact as rollback evidence.

Azure-rendered monthly PDFs and readback manifest:
`output/pdf/AlphaWorld_MS_Azure_Copy_v14r12/` (January 2025-September 2026).

Checked archive: `output/pdf/AlphaWorld_MS_Azure_Copy_v14r12_All_21_Months.zip`;
SHA256 `306fb301834a13106547384aa1a7e033729c4af542c1f722e0338a54c593f5dd`.
Archive CRC and all 21 extracted PDF hashes match the installed cloud outputs.
