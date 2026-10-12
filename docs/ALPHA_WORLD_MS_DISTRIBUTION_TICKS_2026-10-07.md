# Alpha World MS distribution X-axis correction

Owner request: add intermediate return labels to the page-one Distribution of
Annualized Returns chart. The accepted normal interval is 25 percentage points.
The website cutover and historical-batch replacement remain paused.

## Scope and invariants

- Shared MS layout: `subject-completed-factsheet-model-v14r13-alpha-world-ms-distribution-quarter-ticks`.
- Labels lie on quarter-return multiples inside each month's existing axis
  bounds; September shows -50%, -25%, 0%, 25%, 50%, 75%, and 100%.
- Faint dotted vertical guides align exactly to these labels.
- No forced -50% to 100% window: July 2026 retains its 150% upper range.
- A future near-flat distribution spanning fewer than two quarter ticks keeps
  its original finer labels, without changing the histogram bounds.
- No changes to histogram bins, samples, mean, descriptive fits, historical
  markers, HKD conversion, HIBOR averaging, interest treatment, or financial
  metrics. Page two and disclosure typography are unchanged.
- Original IBKR files, reporting payloads, and approved design assets remain
  unchanged. Alpha World T's distribution was byte-identical to the frozen
  installed baseline in the deterministic comparison.

## Qualification evidence

- Local: 42 targeted tests passed; all 21 months rendered; all 42 pages compared
  to the previous renderer. Financial calculation metadata is identical.
- Outside the requested tick/grid region, page one is pixel-identical. Page
  two is entirely pixel-identical. All normal ticks are 25 points apart and
  text bounding widths pass the label-spacing check.
- PDF-skill visual review: all 21 distribution crops and full September page.
- ACR native build: `cka5`; 166 Linux regression tests passed.
- Exact candidate image:
  `ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:f7aaa6935794c7a2ce1ef94ed177fa3f987fd31df8df7d6b56f644242922a590`.
- Vulnerability gate: 0 critical, 0 high, 0 unknown; 40 medium and 19 low
  findings retained from the baseline. This is not a zero-vulnerability claim.
- Frozen build contains only the scoped renderer/validator/test updates on the
  installed v14r12 baseline. Undeployed website/worker drafts are excluded.
- Evidence: `.work/alpha-ms-distribution-v14r13-20261007/`.

## Azure rollout status

Completed: both reporting jobs use the v14r13 image. The candidate and rollback
probes each qualified all 21 actual frozen HKD snapshots. Cloud PDF acceptance
compared all 42 pages to the exact previous Azure artifacts; financial metadata,
snapshot IDs and payload hashes match. Visual review covered every distribution
and both complete September pages. The installed replay also passed all 21
months, with byte-identical PDFs to the accepted candidate.

Executions: candidate `ibkrv2stg-month-end-factsheet-9w0meca`; rollback
`ibkrv2stg-month-end-factsheet-m6yjvab`; installed
`ibkrv2stg-month-end-factsheet-buhjri1`.

Final artifacts: `output/pdf/AlphaWorld_MS_Azure_Distribution_Ticks_v14r13/`;
ZIP: `output/pdf/AlphaWorld_MS_Azure_Distribution_Ticks_v14r13.zip`.
Private Azure output prefix:
`internal-reports/review/alpha-world-ms/distribution-v14r13-20261007/installed/installed-outputs/`.
All older artifacts are preserved. Normal approval/publication gates remain.
Future MS rendering through either reporting entrypoint uses the correction;
this is not authorization to activate onboarding or release reports.

The only deployment targets were `ibkrv2stg-onboard-render` and
`ibkrv2stg-month-end-factsheet`. Their non-image settings, identities, schedules,
credentials and retry limits were verified unchanged before and after
installation. No website revision, onboarding activation, report release,
source mutation or database write occurred. The legacy website historical batch
was not replaced; that separately paused work remains outstanding.
