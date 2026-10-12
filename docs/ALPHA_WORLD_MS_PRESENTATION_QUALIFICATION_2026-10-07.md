# Alpha World MS HKD/HIBOR presentation qualification

## Status

Four-account viewer access verified, 8 October 2026 (12:45 UTC):
the owner's new request authorized read access for all existing viewers to all
four onboarded subjects. Live inspection found four active registered viewers,
each already entitled to the other three subjects but lacking an MS entitlement.
All four subjects and their viewer-access/website-analytics features are active:
Alpha World MS, China & Hong Kong, U16088571 (U.S. Technology and Biotechnology),
and U16088606 (Greater China).

Exactly four missing `read` grants for `alpha-world-ms` were inserted in one
serializable transaction through the existing restricted service login. All
previous entitlements, users, roles, subject lifecycles and feature settings are
unchanged; no higher permission was added. A truthful agent audit event records
the scoped request. No report, source data, image, schedule, authentication or
credential changes were required. Probe execution
`ibkrv2stg-onboard-render-4lrmoyk` and apply execution
`ibkrv2stg-onboard-render-0rt3wnb` passed. Apply proof SHA-256 is
`4d331ee5b36eb02048517617497fc625722a285be4cb08f446da7e8ea6008a71`.

Native read-only application verification passed for all 16 viewer/account
combinations on the exact installed website image. Each registered viewer's
account directory exposes exactly these four active subjects; each account
overview returns HTTP 200 without an owner-only onboarding-preview banner.
Administration and onboarding pages remain HTTP 403 for every viewer;
anonymous MS access is denied. Seven targeted local authorization/scope tests
also passed. Registered viewer claims were simulated only inside the private,
non-ingress manual execution; separate viewer browser sign-ins and the Entra
gateway were not tested or claimed.

The original read-only check failed safely because the reporting worker defaults
to legacy authorization, unlike the website's explicit `database` setting.
Its source, intent and failed execution
`ibkrv2stg-onboard-render-65wzha4` are preserved. A separately bound v2
continuation confirmed the actual live website image and authorization mode,
then supplied only that nonsecret setting to its transient check. It made no
persistent job/website changes or new grants. Successful verification execution
`ibkrv2stg-onboard-render-kbncgko` produced proof SHA-256
`21090b4e03bcb52ce51de00977609edc20eb77dc83742f5701cbbd1b6cb588f9`.
Evidence is under `.work/four-account-viewer-access-20261008-v1/` and protected
private Azure acceptance receipts. The existing owner completed MS onboarding
after the earlier 12:18 checkpoint below; this permission update neither
performed nor bypassed activation. Readers already signed in may need to
refresh the Accounts page to see MS.

Earlier checkpoint (private-preview state before the subsequent owner activation):

Owner-authorized live website cutover completed, 8 October 2026 (12:18 UTC):
the owner's continuation authorized the explicitly scoped coordinated website,
daily benchmark-worker and MS-only HKD comparison update. It did not authorize
preview sign-off, report changes or final subject activation.

The exact frozen database writer first passed in Azure under the restricted
service login using only temporary tables. Failure after 100 inserted comparison
rows followed by caller commit left no partial state. Successful apply and exact
selection rollback restored all original mapping definitions while retaining new
comparison history. Persistent before/after fingerprints remained equal.
Qualification execution was `ibkrv2stg-onboard-render-6r9ncdm`; proof SHA-256
`705b384402b8f17c03c13ac6e70bdc4ac104cc9cd67b590dd213a3491d9998c2`.
Twelve targeted writer/deployment/recovery tests also passed. Test fixtures emitted
SQLite connection ResourceWarnings; there were no test failures. This is not a
claim that the full regression suite or a full live rollback ran in this turn.

Website `ibkrv2stg-web` and daily benchmark worker
`ibkrv2stg-benchmark-daily` now use the qualified immutable image
`5e4edb964065bd2e1d802610117b9ce9c2e13f666d87c969ae3e969731bebfef`.
The website's new revision `ibkrv2stg-web--ms-bench-hkd-20261008` was verified
healthy, running and receiving 100% of traffic. Authentication was checked
separately; credentials, commands, resources, schedules and all other non-image
settings match the protected baseline. The manual reporting worker remains on
`af5d255d4f71f762db763fa7bb8ab542e9c6f52a211c48b51cb1f44deb488fe9`.

One serializable MS-only live transaction closed the original four USD mappings
as historical intervals and installed S&P 500 (`GSPC`) primary plus Hang Seng
(`HSI`) secondary, both reporting in HKD. It inserted 853 versioned HKD
comparison rows (431 GSPC, 422 HSI), preserving all legacy USD/v1 history and
other subjects' mappings and comparison records. Shared prices, FX, NAV,
IBKR sources, contract version/hash, report assets, batch and approvals remained
unchanged. A truthful agent audit event identifies the owner-authorized cutover.
Apply execution `ibkrv2stg-onboard-render-gt9f12b` succeeded; proof SHA-256
`4fdd10e1a3d770a1f64d06da91d5a8c867897c4510a0f14b5277a101af2db735`.

Separate read-only verification `ibkrv2stg-onboard-render-4cpstg5` rechecked
every comparison row, current mapping selection, all preview gates and unchanged
public fingerprints. Proof SHA-256 is
`f6b6d762d77804a99402e1930ba673b5cac136350446a656bd48353206b46043`.
All six private-preview validation checks pass; current evidence SHA-256 is
`4133ff1bd19cd718eefd1afbc17d848497d9f9342b971b393ac2be52105c2c35`.
Latest NAV is 7 October 2026, with 448 inception-based HKD observations.

The computer-use skill's browser workflow verified the owner's authenticated
Chrome view: the selector contains exactly S&P 500 primary and HSI secondary;
both charts and all sixteen displayed benchmark metric values match the
qualified numerical results at display precision. The September historical
date filter also returns a period ending 30 September, rather than the latest
period. Aligned comparison dates and HKD currency are explained explicitly.
Existing restricted-provider/internal-use notices remain; client distribution
has not been enabled and Bloomberg integration remains outside this cutover.

All 42 files were downloaded through the actual owner-session website links and
their full hashes, byte counts, route IDs and monthly pair inventory checked
against the protected registered batch: 21 factsheets and 21 presentations
match exactly. Approved batch
`a46bea9b-ed50-4942-9dda-bc7e28d05b07` / manifest
`b64c4a187ebf772da7ae81ed9a9b19b88a418b2c5e97453f8d58516dc2b22e24`
is preserved. Factsheets retain their approved HKD/HIBOR calculations,
HIBOR + Spread heading and latest layouts. Neither reports nor templates were
regenerated or changed for this website cutover. Downloaded review copies were
left in the owner's Downloads directory; none were deleted.

Protected completion, exact source bindings, apply/verify proofs, Chrome download
inventory and screenshots are under
`.work/alpha-ms-website-release-20261008-v1/live-cutover-v1/`; cloud acceptance
receipts are recorded in `completion.json`. The binding SHA-256 is
`0e79ebf5de9fed7c625033be8ee06697638d6698415ad3ced4631fe46cc87161`.
Chrome is left at step 8 with consent unchecked. The next gate is the owner's
private-preview review/sign-off; step 9 activation remains separately locked.
No new approval, operating registration, report release or activation was written.
MS's ordinary active-registration selector remains unchanged; scheduled MS
processing is not claimed active before final activation. Full live rollback
has not been exercised; isolated exact-writer rollback and credential-free
Azure image recovery were qualified before release.

Earlier checkpoint (superseded by the authorized live cutover above):

Website release-safety continuation, 8 October 2026 (11:01–11:37 UTC):
the fresh read-only capture `ibkrv2stg-onboard-render-xvt7ug4` includes 448
HKD NAV observations through 7 October, 431 GSPC prices and 422 HSI prices.
Protected input SHA-256 is
`4bd539b372bab2b8f9056dd146640d8749d690a5cb89df56036634ff372016ce`.
The prior 6 October capture/proofs below are preserved, not overwritten.

Exact-image, no-overlay native qualification
`ibkrv2stg-onboard-render-5q5e3j0` passed on candidate
`5e4edb964065bd2e1d802610117b9ce9c2e13f666d87c969ae3e969731bebfef`.
All 853 comparison rows passed the independent eight-field replay; a separate
unchanged numerical oracle verified all 40 website summary fields through
7 October. Native proof SHA-256 is
`f5810ba7d99d4137c5b3813515f81255ccb71b72890fe95dd435822be49a05aa`.
The actual candidate readers and preview validator passed in temporary tables.
Selection rollback also restored the original four USD mappings exactly,
preserved both USD/v1 and HKD/v2 comparison history, then restored the candidate
selection and rechecked preview readiness. All public before/after fingerprints
remained equal. This is isolated database qualification, not a live DB rollback.

Two credential-free, manual-only scratch Azure jobs qualified real ARM image
changes and recovery from exact protected baseline versions:
`ibkrv2stg-ms-web-rb-20261008` and
`ibkrv2stg-ms-bench-rb-20261008`. Each candidate and restored-baseline inert
execution succeeded. The website check also proved anonymous comparison reads
stay denied without database access. Both scratch jobs were removed and their
absence verified; protected definitions and evidence remain recoverable.
No normal website/benchmark command ran on these jobs, no database credentials
were supplied, no schedules were enabled, and no RBAC was changed. Azure-added
read-only assigned-identity IDs and an omitted empty secrets array required a
separately protected normalization continuation; identity resource keys, registry
bindings, credentials, environment and all operational settings remain strict.
Full live rollback has not been exercised or claimed.

Additional native benchmark-command qualification
`ibkrv2stg-ms-bench-rb-20261008-78tqb0r` passed with inert fixtures and no
persistent database connections or outbound market requests. The normal
`scripts/refresh_provisional_benchmark.py` imports the qualified producer and
preserves active-registration/lifecycle selection and another-account processing.
Its initial v1 transient start was rejected HTTP 400 before execution; absence
of that payload was checked and preserved. Separate v2 omitted only null
template defaults and passed. The installed benchmark job's explicit account
override remains empty; no MS operating registration or activation was changed.

Current live baselines were re-read and preserved unchanged throughout:
website image `f7aaa6935794c7a2ce1ef94ed177fa3f987fd31df8df7d6b56f644242922a590`,
benchmark image `e031b483982aad3ccdc402ee56345b09c3cc67314e34b12322021d2de0082443`,
and manual reporting image
`af5d255d4f71f762db763fa7bb8ab542e9c6f52a211c48b51cb1f44deb488fe9`.
Authentication was compared separately. The approved 42-file historical batch
`a46bea9b-ed50-4942-9dda-bc7e28d05b07` and approved calculation-contract version
2/hash `a257272638bf2e99626cacdfcfd3977b2cfade9036b00b485446ac38a95db7fc`
are unchanged. 55 targeted local tests and 38 subtests pass, including the new
rollback, fresh-runtime and entrypoint safeguards. Evidence/controllers are under
`.work/alpha-ms-website-release-20261008-v1/` and the corresponding scoped
scripts. No production mapping, comparison, approval, report, source observation,
website image or benchmark image was changed by this continuation.

The next action is owner authorization for a coordinated, website-only cutover:
the website and daily benchmark images plus only MS HKD S&P 500/HSI mapping
selection and versioned comparison history. Preserve the original USD history,
approved reports, HIBOR + Spread factsheet heading, approved HKD/HIBOR methods,
authentication, credentials, schedules and all other subjects. The exact atomic
database writer must still pass isolated qualification before a production
write. Deploy the fail-closed website reader before new HKD selection; rollback
restores the original mapping selection before old images. Refresh drift,
freshness and security gates at execution time. Authenticated live website and
report-download acceptance comes after deployment. Owner preview approval and
final onboarding activation remain separate; neither is authorized by this
release-safety checkpoint.

Verified website-only checkpoint, 8 October 2026: the owner clarified that the
website must retain S&P 500 as primary and HSI as secondary, both compared in
HKD. The latest factsheet's HIBOR + Spread heading, approved HKD/HIBOR
calculations and report layout must not change. No Bloomberg or agentic rollout
is included. This supersedes the benchmark-clarification question in the earlier
checkpoint below, not its recorded historical observations.

The private read-only Azure input capture passed as
`ibkrv2stg-onboard-render-ap8mh12`; protected input SHA-256 is
`121521a35c4edfe491bc983c2df1716f80af353f13e59cde2fddc1202507e02a`.
It contains 447 HKD NAV observations, 430 GSPC prices, 421 HSI prices and
448 dated direct USD/HKD rates plus their retained inverse observations.
There are no external MS cash flows in this captured history. The four current
production mappings still target USD; no production mapping was amended.

Actual PostgreSQL temporary-table rehearsal
`ibkrv2stg-onboard-render-jpvde86` passed on the installed reporting image
`af5d255d4f71f762db763fa7bb8ab542e9c6f52a211c48b51cb1f44deb488fe9`,
with hash-bound website-only source overlays (not a newly installed candidate).
All 851 new HKD comparison rows passed an independent eight-field source replay.
GSPC's common-date period is 21 January 2025 through 6 October 2026 (430 rows);
HSI's is 20 January 2025 through 6 October 2026 (421 rows). These different
starts are explicitly disclosed in the prepared website label. Both actual
website comparison readers and the private preview validator passed.
Failure injected after GSPC writes, during HSI validation, proved rollback of
the whole MS refresh, including after the caller commits a failure record.
The HSI secondary index is mandatory for MS, rather than optional. All legacy
USD comparison rows and all public before/after fingerprints were unchanged,
including mappings, comparisons, FX/prices, contracts, NAV, report snapshots,
assets, historical batches/items and source-account IBKR row hashes. The
approved 42-file batch remains Approved. No reports were rendered/released,
no live preview approval was submitted, and onboarding was not activated.

Protected runtime proof SHA-256 is
`5be158860527ab3af36ed52c4df834ac038c340f6fd57b2de8a00949afce8141`.
Independent equations also verified all 20 website summary fields for each
index (40 fields total), including aligned dates/counts, compounded return,
CAGR, volatility, tracking error, information ratio, beta, correlation,
drawdown and upside/downside capture. Source and actual reader metrics match.
Proofs are under
`.work/alpha-ms-website-comparisons-hkd-20261008-v1/postgres-rehearsal-v6/`.
Earlier failed/private preparation records are retained: v1 could not launch
because a command argument was too large; v2 stopped before dispatch on its
size guard; v3 failed decoding its Base85 transport; v4 was prepared but never
dispatched; v5 stopped on a test-harness SQL alias typo. The accepted v6 uses
bounded Base64 arguments and a temporary audit sequence. No prior evidence was
overwritten or relabeled.

128 targeted tests and 42 subtests pass locally, including independent metric
oracle fixtures. The corrected scoped candidate is frozen under
`.work/alpha-ms-website-benchmark-candidate-20261008-v2/`. Native ACR run `ckab`
passed all 63 tests and exact inherited hashes for 13 approved reporting files.
Image digest is
`5e4edb964065bd2e1d802610117b9ce9c2e13f666d87c969ae3e969731bebfef`.
The CLI returned no build acknowledgement; the existing native run was recovered
against its protected tag, registry and creation-time intent, not resubmitted.
The rejected v1/`ckaa` build remains preserved: its approved-report hash check
passed, but a test helper imported an unavailable deployment-controller module.
V2 separates pure environment verification from deployment tools; the four
PostgreSQL-qualified application modules are byte-identical between candidates.
The exact v2 digest's protected vulnerability scan/SBOM passed: Critical 0,
High 0, Unknown 0, Medium 40, Low 19. These remaining non-blocking findings are
not claimed resolved. All 57 registry blobs were hash-verified; scanner package
coverage includes Debian, Python and Node. No Azure credentials were passed to
the scanner. Its website source is derived from the previously qualified v14r13
baseline plus only comparison-reader, preview-validation and explanatory-label
changes. The unrelated local report-design editing feature is excluded.
Exact-image native runtime passed as `ibkrv2stg-onboard-render-ft7e3hg`, with
no application source overlays. All four installed application module hashes
and the 13 approved reporting-file hashes matched the candidate contract.
The temporary PostgreSQL failure rollback, 851 comparison-row numerical checks,
actual website readers and preview validation passed again. Public fingerprints
and legacy USD rows remained unchanged; the 42-file historical batch remained
Approved. Native proof SHA-256 is
`d77e967b0cbe8bba9f00eb893454b9369ef7a3410cd8bde8bf97a8fef6ddecf1`;
protected evidence is under the v2 candidate's `native-runtime-v1/` directory.
The native reader outputs also match the separately protected independent
20-field equations for both benchmarks (40 fields total). This transitive
numerical binding is sealed in `native-runtime-v1/independent-metrics.json`,
with both actual runtime proofs, identical source inputs/application hashes
and the original independent oracle receipt bound into the new record.
This is an exact-image database/reader rehearsal, not a live authenticated browser
or deployment rollback qualification. Release rollback qualification and the
governed MS mapping/comparison amendment still precede any coordinated live
website/benchmark cutover. Running website
and job images, credentials, authentication and all production data remain
unchanged. Owner preview approval and final activation are separate later gates.

Earlier checkpoint, 8 October 2026: signed-in owner Chrome confirms batch
`a46bea9b-ed50-4942-9dda-bc7e28d05b07` / manifest `b64c4a187ebf772d...`
is Approved, with 42/42 files validated. No duplicate approval was submitted.
Step 8 is current; preview approval and final activation remain blocked.
Live preview evidence is `97bd07fdc49b3ea1debe5b0f3b2b8b755376dc2c3c352343fdc4f27f9a2d1f9e`.
The four comparison mappings still report USD, while MS reports HKD; Shanghai's
30 September comparison also exceeds the preview's four-calendar-day limit
at the 6 October NAV cutoff. The locked financial page names S&P 500 Price Index
as primary, despite the requested factsheet HIBOR + Spread heading. The numeric
HIBOR benchmark tenor/spread/accrual contract is not defined by the separately
approved 1M HIBOR period-average risk-free rate.
Local preparation adds an integrity-bound exact-last-session closure exception
and restricts preview comparison counts to matching actual/mapping currencies.
122 tests and 23 subtests passed, including wrong-currency, absent-row,
future-date, uncovered-calendar and reopened-market rejection. These changes
are NOT deployed; no live comparison, mapping, approval, gate or IBKR source was
changed. Owner benchmark clarification is required before a governed benchmark
amendment; web release qualification/explicit scoped cutover approval, private
preview review/approval and separate activation remain. Append-only observation
is `.work/alpha-ms-preview-review-20261008-v1/observation.json`; this is a local
browser observation, not an independently protected Azure database receipt.
Earlier pending-approval records below are preserved historical checkpoints.

Current five-step checkpoint, 8 October 2026: independent 21-month candidate
acceptance and reporting rollback qualification passed. Both persistent reporting
workers now use format r4 digest `af5d255d4f71f762db763fa7bb8ab542e9c6f52a211c48b51cb1f44deb488fe9`.
Their non-image settings are verified preserved. Inert installed checks passed
for 42 installed files on each worker, including scheduled execution
`ibkrv2stg-month-end-factsheet-7bbn6vx`. Protected release completion is sealed.
A new installed-image private render was dispatched once as
`ibkrv2stg-month-end-factsheet-7r35r94`, using candidate-specific prefix
`review/alpha-world-ms/format-worker-r4-20261008/installed-private-batch/`.
The fresh render passed all 21 months and readback of 42 files. All 21 PDFs
are byte-identical to the accepted candidate. Independent checks pass 126
financial charts, 147 risk metrics and 126 editable workbooks. Fresh previews
match all 420 accepted slide PNGs and 42 accepted PDF page PNGs exactly.
PowerPoint package bytes differ only in generated relationship identifiers and
core timestamps: `semantic-package-v2.json` proves all 21 resolved packages
equivalent, including unchanged binary/workbook parts. Its first diagnostic v1
remains preserved. Twenty-one package/registration/download/check tests pass.
The private historical batch replacement is complete and protected. New batch
`a46bea9b-ed50-4942-9dda-bc7e28d05b07`, manifest
`b64c4a187ebf772da7ae81ed9a9b19b88a418b2c5e97453f8d58516dc2b22e24`,
supersedes only unapproved batch `86e6c5fe-bc53-40f2-bd53-aad8ff0be84f`.
Old files/items/generation records remain preserved. Read-only baseline execution
`ibkrv2stg-month-end-factsheet-6alvc4g`, one-shot registration
`ibkrv2stg-month-end-factsheet-9bck54o` and independent read-only verification
`ibkrv2stg-month-end-factsheet-4syyljp` all passed. The new 21 PDF/21 PPTX
inventory is ready for owner approval, not released or activated.
All 42 observed owner-session Chrome routes (880 through 921) were downloaded
and their actual bytes/sizes matched the registered inventory. The 21 PDFs
received Chrome's normal ` (1)` duplicate suffix; the checker explicitly bounds
that suffix and still requires exact registered bytes and all distinct route IDs.
Forty-two tests and 26 subtests pass. Protected website download acceptance
passed and is retained under `registered-batch-v1/website-downloads-v1/acceptance.json`;
all 42 actual downloaded files match their registered bytes and sizes. The consent
checkbox remains unchecked and approval button disabled. New owner approval
evidence displayed by the website is
`baeefe2c54afbe501a1caf8122824ca18a4e7f78bd237d77b8010cb4644937a3`.
Steps 1-3 of the requested five-step continuation are complete. Step 4 requires
the owner's exact new batch approval, then separate private-preview inspection
and approval. Step 5 is final activation only after those gates. No new native
Microsoft PowerPoint interaction is claimed by this continuation.
Protected before-state,
one-shot operation intents and qualifications are in the format r4 candidate's
`deployment-v1` directory. Owner report approval, preview approval, activation,
website image, credentials, RBAC and IBKR source observations remain unchanged.

Current five-step release checkpoint, 8 October 2026: all 21 installed-image
fallback factsheets passed byte identity, and the isolated candidate startup,
image-switch, exact-version archive restore and restored-image startup passed.
The temporary `ibkrv2stg-ms-rollback-20261008` job is verified removed; its
configuration and operation evidence remain recoverable in the protected archive.
Both persistent reporting jobs remained unchanged throughout that rehearsal.
Scoped worker release preparation is now underway. This is a reporting rollback
qualification, not a full database/application disaster recovery exercise.
The signed-in owner website shows the earlier private batch still awaiting approval
(manifest prefix `688297133906a87b`); preview and activation are locked.

Five-step continuation, 8 October 2026: the format r4 Azure execution
`ibkrv2stg-month-end-factsheet-s8rwakv` passed all 21 months and exact downloaded
output integrity. Independent all-month audits pass 126 charts, 147 risk metrics,
126 embedded workbooks/cells and all package/layout checks. The 420 re-rendered
slide PNGs exactly match the separately reviewed number-format v2 baseline.
All 42 re-rendered PDF page PNGs and their 21 report files exactly match the
protected prior full-page inspection; acceptance is explicitly inherited through
identity, with zero new page-inspection claims. Protected candidate render/output
acceptance is retained in `runtime-v1/candidate-render-acceptance.json`.
Twenty-six acceptance/controller tests and 49 recovery/release tests pass.
The installed-image private fallback was dispatched once as
`ibkrv2stg-month-end-factsheet-dq0ircb`; completion and isolated image-switch
recovery remain to be qualified. No worker deployment, batch replacement,
owner approval or MS activation has occurred in this continuation.
An isolated manual-only rollback rehearsal job was provisioned as
`ibkrv2stg-ms-rollback-20261008`, with an inert import/check command and the
existing identity/secret references. Its creation is not a production image
switch. The protected before-state and per-operation intents remain under the
format r4 candidate's `rollback-rehearsal-v1` directory. It must be restored,
smoke-tested and removed before its recovery gate can pass.

Owner instruction on 8 October 2026: resume Alpha World MS onboarding work.
Preparation resumes at the pending exact-image, private 21-month Azure render
qualification. This instruction is not recorded as approval of a report manifest
or website-preview evidence, and does not bypass the final activation gates.
IBKR source observations remain immutable. The runtime controller now binds the
format candidate to the protected installed r5 check rather than the superseded
v14r13 image check; 19 controller/build tests pass. The candidate image itself is
unchanged. Runtime preparation succeeded in the candidate-specific private prefix;
no persistent worker, approval or lifecycle settings were changed by preparation.
The private candidate execution was dispatched once as
`ibkrv2stg-month-end-factsheet-s8rwakv`, using the qualified format r4 digest.
Dispatch acknowledgement confirms persistent settings were unchanged. Completion,
all-month output acceptance and rollback are not inferred from that acknowledgement.

Latest checkpoint, 8 October 2026: scoped format-worker candidate r4 passes
104 isolated regression tests and exact reproduction of all 21 privately
reviewed monthly presentations (126 financial charts and 147 risk metrics).
The shared MS output gate now requires explicit number formats and the HKD
editor width. Fresh private rendering now passes using a strictly bound,
desktop-only compatibility adapter; independent structure/layout checks pass
for all 22 files. Azure native candidate build `cka9`, all 104 native tests and
the exact-image security gate with protected readback have passed. Live
exact-image all-month rendering and rollback qualification remain pending.
The local adapter is not in the candidate image. The installed reporting image,
reports, approvals, onboarding and IBKR source data remain unchanged.
Representative native editor evidence and the owner's all-month opening
confirmation remain preserved; the latter is not independent file observation.
See the final continuation section for exact evidence and remaining gates.

### Earlier qualification checkpoints (preserved, not current release status)

Private September presentation: local financial and rendered-layout acceptance passed.
All twenty-one monthly presentations now pass local exported financial, package
and rendered-visual qualification in the corrected r4 batch. All 420 slides are
covered by explicit full-size review or exact PNG identity to reviewed images;
none remain pending. Native Microsoft PowerPoint rendering is not verified.
The reporting-worker changes are NOT deployed. The historical website batch has NOT
been regenerated, superseded, registered or released. MS activation is unchanged.
This is an intermediate qualification step, not completion of the full programme.

Scoped candidate r2 has now passed isolated local qualification, Azure native
build/runtime tests and the vulnerability/SBOM gate with protected readback.
The live render-only runtime and installed-image factsheet fallback checks also
passed. Release acceptance is BLOCKED: the Azure presentations omit the six
embedded chart-data workbooks per deck present in the accepted private version.
Visual acceptance and a live settings-switch rollback are not complete. See the
final continuation section below. This does not change the undeployed status above.

Latest continuation: the missing-workbook defect is corrected in scoped candidate
r5, with mandatory workbook/cell validation shared by both MS workers. Local
all-month replay, independent structure checks and Azure/Linux build tests have
passed, as has the exact-image security gate with protected readback. The new
image is not deployed. Its repeat Azure all-month render and downloaded-output
visual checks now pass; recovery/live-switch rollback remains separate. The older r2 live
outputs remain rejected, not retroactively accepted by the local correction.

## Inputs and boundaries

- Execution-only read-only Azure diagnostic: `ibkrv2stg-onboard-render-681wnaq`.
- Twenty-one active frozen HKD snapshots, January 2025 through September 2026,
  downloaded from the private `internal-reports/review/alpha-world-ms/presentation-hkd-20261007/inputs/`
  prefix. All stored payload hashes matched and all metrics recomputed unchanged.
- The diagnostic reports zero database writes, no IBKR source modification,
  no report release and no onboarding activation. The persistent job settings
  remained unchanged. No identities or permissions were added.
- Locked platform layout is the approved August v24, 20-slide presentation.
  Its source SHA-256 remains
  `4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191`.
- Owner-intake identity metadata was reused from previously qualified Azure
  evidence. The controller lacks direct container access and did not fetch those
  intake bytes or widen its permissions. The production worker must revalidate
  current intake bytes and approval evidence before any batch registration.

## Implemented, not deployed

The MS-only model `alpha-world-ms-presentation-hkd-hibor-v1` recomputes the
qualified HKD risk metrics. It validates component NAV and gross-exposure
reconciliation and monthly contribution reconciliation. It derives monthly
returns from observed HKD NAV, including the opening partial January 2025.
Source snapshots remain unchanged.

The renderer preserves the approved layout and other subjects' USD route.
It clears unused reference-account cells, shows missing synthetic quantities
as N/A, uses HKD values per initial NAV 100 and the inception-to-valuation
published 1M HIBOR average. Financing effects already in NAV remain included
without an additional charge. No qualified benchmark/spread curve, crisis
replay or simulated drawdown results are invented: these show N/A or UNAVAILABLE.

Layout fixes include shorter component-table descriptions, vertical cell padding
without smaller fonts, corrected horizontal chart value-axis controls, explicit
contribution-magnitude headings with signed labels, and an unclipped cash-policy
caption. Original approved photographs, branding and appendix content remain.

## September acceptance

Qualified private file:
`.work/alpha-ms-presentation-hkd-20261007/qualification/AlphaWorld_MS_Presentation_2026-09-30_HKD_Review_v7.pptx`

- Output SHA-256:
  `8a699cb4264cb7347ab7e8762ba7e75f11ed977be8561923f708ae63a0d8852a`.
- Frozen input SHA-256:
  `fcfe8b88caca01f11476e603797e8353f3b71835570b471a95775bd113d51f2e`.
- Exported XML audit passed all six native financial charts' categories, values
  and value-axis units, plus signed contribution labels and magnitude disclosure.
- Seven risk-table metrics matched recomputation. Observed full-period drawdown,
  returns, source-backed cash and unavailable-data disclosures also passed the
  existing content and source-model checks.
- Twenty slides, eleven native tables and six native charts retained. Literal
  chart data materialized into workbooks and package/import checks passed.
- All twenty final rendered slides individually inspected. No native-table
  overflow warning remains. Seven retained outside-slide-boundary warnings are
  the approved template's cover/background/contact furniture; their rendered
  visible content was inspected. No claim of zero warnings is made.
- Font policy preserves Arial, Aptos Display and Georgia. The deck was reviewed
  through rendered previews, not through Microsoft PowerPoint itself. Native
  PowerPoint font rendering is not independently verified.
- Thirty-one targeted tests and thirteen subtests passed. The repeated read-only
  model check passed all twenty-one unchanged Azure input hashes. Node syntax
  checking passed. This is not full repository regression or all-month deck QA.

The earlier private drafts remain as diagnostic evidence and are not accepted
outputs. One local finalizer attempt used a runtime without lxml; it was rerun
with the configured bundled runtime. A test replay without the configured
workspace TEMP failed during sandbox directory cleanup; the correctly scoped
replay passed. Neither failure changed Azure or source data.

## Evidence

- Input evidence: `.work/alpha-ms-presentation-hkd-20261007/inputs-qualified.json`.
- Final render review: `.work/alpha-ms-presentation-hkd-20261007/qualified-v7-review/`.
- Final package and financial receipts:
  `.work/presentation-validation/AlphaWorld_MS_Presentation_2026-09-30_HKD_Review_v7-01ba2e1e-64aa-4eca-9835-a24ccdc5ee07/`.

## All-month private qualification continuation

The corrected private batch covers January 2025 through September 2026:
`.work/alpha-ms-presentation-all-months-20261007-r2/`.

- Twenty-one presentations rendered; 420 final slide PNGs and 420 layout exports
  produced. All 126 native financial charts and 147 risk-table metric values
  passed the exported-package audit. There were no failed months.
- The approved physical template and all twenty-one frozen input hashes remained
  unchanged. Renderer, source model, content-expectation and exported-audit hashes
  are bound in `input-and-code-binding.json` and were checked again after the batch.
- Every output is bound to its exact SHA-256, financial audit and finalizer receipt.
  Package and layout error findings are zero. New warning kinds/objects fail closed;
  the seven approved template-furniture boundary warnings remain disclosed.
- The first private batch, `alpha-ms-presentation-all-months-20261007-r1`, is
  preserved but not visually accepted. January has only one observed month-end:
  its line-only chart had no visible point. The MS-only renderer now gives that
  one actual observation a native circle marker, without adding a synthetic date
  or data point. Exported marker validation rejects missing, disabled or zero-size
  markers. Other subjects and multi-month series retain their existing rendering.
- Thirty-five targeted tests and eight subtests passed after this correction.
  Node syntax checks and Python compilation passed. This does not represent full
  repository regression, Azure runtime qualification or release approval.

### Earlier r2 partial visual coverage (superseded by r4 below)

January's twenty first-pass slides were individually inspected; the corrected
January render differs only on slide 7, which was separately inspected full-size.
April's fifteen dynamic slides were individually inspected, including negative
returns, signed contribution labels and the seven risk metrics. Its five static
slides are pixel-identical to the inspected January slides. September's final
twenty PNGs are pixel-identical to the previously inspected September v7 render.
The same five static slides are identical in every other monthly deck.

This yields 150 covered slides and 270 still awaiting visual review, recorded with
exact preview and output hashes in `visual-review-coverage.json`. Pixel identity is
only reused for already inspected images; no automated layout check is presented
as manual visual inspection. Microsoft PowerPoint native rendering remains
independently unverified.

Remaining copy review includes slide 10's inherited heading, "Sector Drivers of
Relative Performance": the MS chart contains absolute component contributions
with signed labels, not qualified benchmark-relative attribution. Clarify this
MS-only heading before final visual acceptance. The explanatory subtitle and chart
unit disclosures do not replace that remaining heading check.

No Azure configuration, website report batch, owner approval, onboarding state or
IBKR source data changed during this continuation. The paired PDF/PPTX cloud batch
has not been generated or registered.

All-month evidence:

- Financial/package summary: `.work/alpha-ms-presentation-all-months-20261007-r2/batch-financial-package-receipt.json`.
- Per-month receipts: `.work/alpha-ms-presentation-all-months-20261007-r2/receipts/`.
- Final previews: `.work/alpha-ms-presentation-all-months-20261007-r2/previews/`.
- Explicit visual coverage: `.work/alpha-ms-presentation-all-months-20261007-r2/visual-review-coverage.json`.

## Completed all-month private qualification: r4

The accepted private batch is now
`.work/alpha-ms-presentation-all-months-20261007-r4/`, covering the same twenty-one
unchanged frozen snapshots and approved physical template.

- All 21 decks passed: 420 slide exports, 126 native financial charts and 147
  risk-table metric values. The financial/package summary has no failed months.
  Exact output, input, code and finalizer hashes are retained per month.
- The remaining dynamic slides were inspected individually at full size. The
  corrected slide 10 was inspected in all 21 final decks. Static slides and
  previously inspected dynamic slides were inherited only through exact PNG
  SHA-256 identity to inspected images. Final coverage is 420/420, zero pending.
- Slide 10 now says "Component Drivers of Monthly Return", reflecting absolute
  component contributions rather than benchmark-relative attribution. This is
  MS-only; the approved geometry/fonts and other subjects' title remain intact.
  Comparison with r2 confirms that only slide 10 changed in each deck: all other
  399 preview images are byte-identical.
- The exported audit requires the corrected MS heading and opts into the
  identity-restricted MS content validator. Its code hash is now included in the
  batch binding. Legacy generic-account title validation remains unchanged.
- The intermediate r3 attempt was rejected because the generic validator still
  required the old heading. Its diagnostic evidence is preserved and not
  accepted. The scoped validator correction was tested before the complete r4
  rerun; no failed r3 output is used as a qualified artifact.
- Thirty-seven targeted tests and eleven subtests passed. This is not full
  repository regression, cloud vulnerability qualification, runtime acceptance
  or rollback verification.
- Rendered preview acceptance is not native PowerPoint application acceptance.
  Seven approved template-furniture boundary warnings remain, with no new
  warning kinds/objects accepted. Qualified benchmark/spread comparisons, HKD
  crisis replay and simulated drawdown remain explicitly unavailable, not zero.

Final evidence:

- Financial/package summary: `.work/alpha-ms-presentation-all-months-20261007-r4/batch-financial-package-receipt.json`.
  SHA-256: `96ad87727ead0be7daebd15b0f57ddfcac743373f2b108489bfc25413f1d0555`.
- Exact review coverage: `.work/alpha-ms-presentation-all-months-20261007-r4/visual-review-coverage.json`.
- Per-month receipts: `.work/alpha-ms-presentation-all-months-20261007-r4/receipts/`.
- Final editable presentations: `.work/alpha-ms-presentation-all-months-20261007-r4/output/`.
- Final rendered previews: `.work/alpha-ms-presentation-all-months-20261007-r4/previews/`.

Azure, the website's historical batch, owner approvals, onboarding and protected
IBKR sources remain unchanged. Local all-month acceptance completes the first
gate; it does not authorize bypassing the remaining deployment/release gates.

## Next gates, in order

1. Bind MS-specific presentation validation to the registered content contract
   and reporting workers, including the opt-in MS heading validator and exported
   financial audit. Local all-month qualification is complete, but worker wiring
   is not yet qualified.
   Preserve other subjects' current contract and fail closed on legacy MS output.
2. Freeze a scoped release candidate on the installed v14r13 baseline, including
   the new model, exported-chart audit and already-prepared MS frozen-snapshot /
   create-only artifact worker changes. Exclude unrelated dirty-worktree changes.
3. Complete ACR build, regression, vulnerability, isolated Azure runtime and
   rollback qualification before updating any reporting worker. A new website
   image change remains a separate live cutover authority boundary.
4. Revalidate owner identity evidence using the existing worker identity. Create
   a new private paired 21-month batch with immutable content-addressed artifacts
   and readback verification, preserving the old batch and protected sources.
5. Verify registered manifests and authenticated downloads for both report types
   before asking for owner report approval. Activation/release is not implicit.

## Reporting-worker output gate: locally connected and replayed

Both `generate_month_end_package.py` and
`render_onboarding_historical_reports.py` now use the same read-only output gate,
`validate_subject_presentation.py`, before presentation upload/registration.

- MS requires the requested subject/period, approved MS strategy identity and
  HKD currency. The existing model and exported audit then validate the frozen
  calculations, all six charts, seven risk metrics, contribution heading,
  signed labels, units, unavailable-data disclosures and native evidence.
- The new MS-specific content contract is
  `alpha-world-ms-v24-hkd-hibor-evidence-v1`. Historical manifests, generation-run
  versions, blob metadata, monthly package versions/audits and portfolio assurance
  select this contract for MS only. Other subjects retain the original platform
  content contract and generic validator.
- Validation receipts bind exact snapshot and presentation SHA-256 values. The
  worker checks these again before upload; a changed file, incomplete audit or
  legacy USD MS snapshot fails closed. Historical review artifacts remain
  create-only, hash-addressed and read-back verified.
- The shared gate replay passed all 21 unchanged accepted r4 decks. The original
  financial and visual evidence remains preserved; the new worker/code binding
  is recorded separately in
  `.work/alpha-ms-presentation-all-months-20261007-r4/worker-output-gate-v1.json`.
- 129 targeted/integration tests and 53 subtests passed, including PostgreSQL
  adapter/security definitions, report runtime, registration, other-account
  compatibility and stale-contract rejection. Two source-location assertions
  were adapted to follow the shared validator while retaining their content
  checks. Python compilation and scoped diff checks passed.
- Read-only live Azure checks confirmed subscription
  `3956e821-cee4-403f-8708-e84e2ed490eb` is enabled and both reporting jobs remain
  successfully provisioned on the expected v14r13 digest
  `f7aaa6935794c7a2ce1ef94ed177fa3f987fd31df8df7d6b56f644242922a590`.
  No cloud settings, executions, database rows or report approvals were changed.

This is local shared-gate/worker-wiring qualification, not end-to-end Azure worker
qualification. The installed frozen baseline is available, but current worker
files also contain unrelated report-design changes. Do not copy the dirty tree
into a release: reconstruct an explicit scoped delta on the installed baseline.
Candidate build, vulnerability scanning, isolated runtime/rollback verification,
deployment and private paired-batch registration still remain.

## Scoped candidate freeze and native Azure build: r2

The installed v14r13 manifest, configuration and all 43 uncompressed layers were
independently hash-verified before selected application sources were read. The
candidate overlays only the MS presentation model/audit/shared worker contract
and create-only review artifact handling. It excludes the unrelated website
report-design feature and preserves other subjects' generic caption behaviour.
No dirty-tree bulk packaging, package upgrades or database migration is included.

- Evidence folder: `.work/alpha-ms-presentation-worker-candidate-20261007-r2/`.
- Frozen context: 23 files, explicitly hashed in `source-intent.json`.
- Source intent SHA-256:
  `e91aa875788ce31307af24e02ff6b04b3ee389c24ef510dbabcf47d384309193`.
- Isolated local runtime: 302 installed Python/MJS source files reconstructed
  from the verified image, then the frozen overlay applied. The approved template
  was recovered unchanged from that same image; repository imports were not used
  in the accepted isolated test run.
- 62 targeted tests passed in that isolated runtime, and all 21 accepted monthly
  presentation files passed the shared gate again without changing their bytes.
  This is validation replay, not a new cloud financial/visual rendering acceptance.
- Native ACR build `cka6` succeeded. Its exact output is
  `ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:477b1fd99cafe4bcc9cbd6b2c9e8da98b317baeadd18131041118c2231c89fb8`.
- The Azure Linux build passed the report runtime/template checks, all 62 tests,
  and the shared-worker contract assertion. See `native-build-qualified.json`
  and the privately retained `native-build.log`.
- The dispatch command returned no acknowledgement. The existing native run was
  reconciled by its creation time and exact tag/image identity. It was never
  dispatched again. Five additional release-controller tests verify rejection of
  wrong identities, wrong timing/status, changed context and acknowledgement
  overwrite; these tests do not alter the frozen image.
- First attempt r1 remains preserved and was not uploaded or accepted. Its local
  failures exposed a full-install Docker recipe assumption and the need for
  identical-artifact resumption on the MS monthly private-upload path. R2 uses
  create-only upload with exact readback and rejects conflicting existing bytes.

The exact candidate registry bytes and all decompressed image layer identities
are verified again before offline vulnerability/SBOM assessment. Scanner
credentials are not supplied to the scanner. Trivy 0.75.0 covered the operating
system, Python and Node package inventories and passed the existing blocking
policy: zero critical, high or unknown findings. There are 40 medium and 19 low
findings; no suppression or exception was used. This is not a vulnerability-free
image. The scan assessed 99 package entries and a 100-component CycloneDX SBOM.
All 49 registry blobs and every decompressed layer were hash-verified.

The exact source/local/native-build receipts, vulnerability report, SBOM and
final assessment are retained create-only in the existing protected archive,
with versioned byte-for-byte readback. See `scan-completed.json` and
`scan-protected-receipt.json`. Both reporting jobs were checked before and after
the scan: their installed image and non-image settings/identity invariants remain
unchanged. The running website, both reporting jobs, report approvals, MS
activation and original IBKR data remain unchanged.

Next: qualify an isolated Azure execution with the existing reporting identity
and verify rollback before any worker image cutover.
Then generate/register the private paired historical batch and verify authorized
downloads. Owner approval/release and account activation remain separate gates.

## Isolated Azure runtime qualification: started

The candidate is being exercised through a transient execution override of
`ibkrv2stg-month-end-factsheet`, using its unchanged identity, secret references,
environment and resource settings. There is no persistent job image update.

- Candidate execution: `ibkrv2stg-month-end-factsheet-4iuad4j`.
- Frozen runner and protected intent:
  `.work/alpha-ms-presentation-worker-candidate-20261007-r2/runtime-v1/`.
- Private prefix:
  `internal-reports/review/alpha-world-ms/presentation-worker-r2-20261007/candidate/`.
- The probe requires the current owner-approved presentation identity and exact
  uploaded identity-source bytes before rendering. It verifies the unchanged
  approved platform template and the installed candidate's source-file hashes.
- It reads the actual persistent frozen HKD snapshots, verifies their existing
  stored checksums, exact correspondence to the prior immutable HKD cutover
  evidence and canonical equality with the previously accepted 21 private inputs.
  It compares each period's risk metrics against both committed evidence and the
  live risk cache. No review-input substitution or reconstruction is allowed.
- PostgreSQL enforces `default_transaction_read_only=on` and a repeatable-read,
  read-only transaction; the probe checks `transaction_read_only` before render
  and rolls back at exit. No approval, onboarding, registry or source row is
  created or modified.
- Both factsheets and presentations are rendered and audited, uploaded
  create-only under the private prefix and read back byte-for-byte. Presentation
  validation uses the exact shared gate exported by both production entrypoints.
- Six controller tests pass: execution-only mutation, preservation of environment
  and resources, refusal to redispatch, busy-worker refusal, frozen-source
  integrity, secret-reference requirement and database read-only enforcement.

At this update the first six periods, through June 2025, passed cloud rendering
and validation. The execution has not yet received a complete 21-month acceptance
receipt. Visual acceptance is explicitly separate from numerical/runtime audit.
The installed-image rollback probe is not yet dispatched; it must wait for the
candidate execution and validation to finish. No deployment is authorized by
this partial result.

## Completed isolated render/fallback checks; editable-workbook blocker

The candidate execution completed successfully for all 21 actual frozen monthly
inputs. Its identity/command/source binding, current owner-approved MS identity,
read-only transaction enforcement and complete coverage were accepted. All 42
private output files were downloaded and checked against the cloud receipt.
The qualified receipt is `runtime-v1/candidate-qualified.json`.

The execution API's template projection initially differed from the submitted
template. Read-only comparison with the previously qualified installed execution
proved the exact existing behaviour: Azure projects `database-url` as
`cappjob-ibkrv2stg-month-end-factsheet`, drops its empty `value` placeholder and
adds an empty `ephemeralStorage` field. Key Vault reference metadata and attached
managed identity were independently checked; ARM identity casing differed only
in representation. Nonempty environment changes, other aliases, changed vaults,
identities and resource settings still fail. No secret value was obtained. No
candidate execution was retried to resolve this controller comparison issue.

- Independent financial/package/geometry audits passed all 21 downloaded cloud
  presentations: 126 financial charts and 147 risk metrics, 20 slides per deck,
  zero package/geometry findings and no new warnings beyond the seven accepted
  template-furniture boundary warnings. This is not rendered visual acceptance
  or native Microsoft PowerPoint testing.
- Independent audit receipt:
  `runtime-v1/cloud-local-audit-v1/receipt.json`.
- Installed-image fallback execution:
  `ibkrv2stg-month-end-factsheet-60co921`. It used the unchanged v14r13 image,
  identity and configuration in another transient execution. All 21 factsheets
  rendered and passed validation; every PDF was byte-identical to its candidate
  counterpart. See `runtime-v1/rollback-qualified.json`.
- Fallback scope is existing MS factsheet rendering, not support for the new
  HKD presentation feature on the old image, and not a live image/settings switch.
  Do not call this full production rollback qualification.
- Eight controller tests pass, including strict observed execution-projection
  handling and the rule that missing workbooks keep overall acceptance blocked.

### Blocking discovery: Azure chart-data finalization is skipped

All 21 accepted private decks have six native chart-to-embedded-XLSX bindings.
All 21 Azure candidate decks lack these bindings: **126 missing workbooks**.
The chart cache values themselves passed the financial audits, but the cloud
output does not match the accepted editable-chart data contract. Do not infer
PowerPoint's Edit Data behaviour from native chart counts alone.

The qualified renderer currently calls desktop finalization only when
`SKILL_DIR`, `RUNTIME_PYTHON` and `RUNTIME_NODE_MODULES` are set. None is configured
on the unchanged Azure worker. Its fallback simply moves the exported candidate
PPTX; it does not run `materializeLiteralChartWorkbooks: true`, unlike private
desktop qualification. This explains the missing XLSX parts/relationships.

The independent, read-only comparison is preserved in
`runtime-v1/chart-workbook-acceptance.json`. The overall programme receipt is
`runtime-v1/programme-acceptance.json`; its status is
`blocked_chart_workbook_acceptance`, not ready for promotion. The passing render
and security receipts remain preserved with their exact narrower scopes.

Next is a new scoped candidate that materializes and validates all six chart-data
workbooks inside Azure without requiring desktop skill paths, with a mandatory
MS shared-gate check for workbook/category/value consistency. Preserve r2 and
its evidence. Repeat the affected build/scan/runtime checks, then complete actual
cloud visual acceptance before any worker deployment or private-batch registration.
Both production reporting jobs, the website, owner report approvals, onboarding
state and original IBKR source data remain unchanged.

## Workbook correction candidate r5

New scoped frozen context:
`.work/alpha-ms-presentation-worker-candidate-20261007-r5`.
It derives from the verified frozen r2 context and the exact installed v14r13
base image, not a broad repository snapshot. The 25-file context includes only
the original reporting overlay plus the explicit MS workbook delta and tests.
The preparer verifies that reversing the workbook renderer changes restores
the exact r2 renderer, including its unchanged generic-account caption path.
No dependency upgrade or desktop skill package is installed into the image.

The MS-only module `ibkr_etl.ms_chart_workbooks` materializes all six native
charts' complete literal frozen data into six embedded XLSX snapshots. It never
reconstructs IBKR observations, invents workbook source formulas, or replaces
an existing invalid workbook. Healthy bound snapshots are verified and copied
byte-identically. Missing data, mismatched categories/values/headers, incorrect
cell ranges, duplicates, external links, formulas, unsafe XML/ZIP content,
unsupported chart structures and nonfinite values fail closed. Outputs are new
files, never overwritten sources. Both workers require this audit before
upload/registration. The MS contract is now
`alpha-world-ms-v24-hkd-hibor-evidence-v2`; other subjects' contracts are unchanged.

Qualification completed so far:

- 83 tests pass in the isolated installed-source runtime and in the native Azure
  Linux build. Eight existing transient-execution controller safeguards also
  pass separately. No unrelated repository imports are allowed in the isolated
  candidate runtime.
- All 21 hash-verified r2 cloud decks are repaired into new private copies and
  pass the independent financial/shared worker gate: 126 workbook bindings,
  every category/value/header, 126 financial charts and 147 risk metrics.
- A fresh September render, with no `SKILL_DIR` or desktop finalizer, contains
  six verified workbooks and passes the shared gate. All 20 rendered PNGs are
  exactly SHA-256-identical to the previously accepted, reviewed September PNGs.
  This is local preview identity, not a new Azure runtime or native PowerPoint test.
- Independent package and geometry inspection passes all 21 repaired decks and
  the fresh September deck: 22 documents, zero findings, no new warnings beyond
  the seven retained template-furniture warnings.
- Native ACR build `cka8` succeeds with image
  `ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:d98e16415d7e4b601571913f2d3be3bc48e62c69d9725419f556a2b71333a584`.
  The CLI did not return a dispatch acknowledgement. This same native run was
  reconciled against the frozen intent and creation time; it was not redispatched.

Evidence: `local-qualification.json`, `local-structure-qualification.json`,
`fresh-smoke-qualified.json`, `build-completed.json`, `native-build-qualified.json`.

The exact r5 image's Trivy 0.75.0 gate also passes: critical 0, high 0, unknown 0;
medium 40 and low 19 remain reported, not suppressed or described as zero
vulnerabilities. OS, Python and Node coverage includes 99 package entries / 91
unique package purls, with 100 SBOM components. All 49 registry content blobs
and uncompressed image layer identities are verified. Raw scan/SBOM and frozen
source/local/native-build receipts are retained create-only in the existing
protected archive with exact version readback. See `scan-completed.json` and
`scan-protected-receipt.json`. Both live reporting job images and non-image
settings still match their captured v14r13 baseline; no production promotion.

Rejected attempts remain preserved: r3 failed before any Azure build because
the parser initially rejected legitimate OPC root-relative chart targets; r4
passed numeric tests but the independent package check rejected content-type
namespace formatting. Its submitted ACR build `cka7` is not an accepted release.
Both issues are corrected and covered in r5. Do not overwrite or relabel those
attempts' earlier evidence as passing.

Next gates: repeat the actual read-only Azure 21-month candidate render with a
new private prefix and the v2
workbook contract; independent downloaded-file and rendered visual acceptance;
recovery/live-switch rollback qualification; then scoped worker deployment and
private historical batch registration. No release/onboarding action is included
in these qualification results. The website editing feature remains excluded.

## Actual r5 Azure output qualification, 8 October 2026

Isolated execution `ibkrv2stg-month-end-factsheet-je75swl` succeeded for all
21 periods, January 2025 through September 2026, using the exact scanned r5
image and mandatory v2 workbook contract. All 21 PDFs and 21 presentations
were written create-only under the new private
`internal-reports/review/alpha-world-ms/presentation-worker-r5-20261007/candidate/`
prefix, downloaded and hash-verified. PostgreSQL enforced a repeatable-read,
read-only transaction. The approved current owner identity and all frozen input
hashes were revalidated. Zero database writes, no original IBKR changes,
no report releases, no onboarding activation and unchanged persistent job
images/settings are confirmed by the protected runtime receipt.

Independent downloaded-file checks pass all 21 months: 126 native financial
charts, 147 risk metrics and 126 embedded editable chart-data workbooks, with
every worksheet category/value/header reconciled to its chart. Package and
geometry checks have zero findings. Seven known approved-template furniture
warnings per deck remain; there are no new warnings.

Every presentation was exported into all 20 slide previews. Of the 420 slides,
399 are byte-identical PNGs to the fully reviewed r4 reference. Each month's
conclusions slide 17 differs in text wrapping. Independent XML comparison
confirms that only known font-autofit scaling differs: the accepted desktop
finalizer scales selected text to 88.662-98.796 percent, whereas the cloud
output retains 100 percent. Text, box geometry and other styling are unchanged.
All 21 actual cloud conclusions slides were displayed and inspected individually;
their content fits within the boxes without clipping or overlap. This is explicit
manual review, NOT a claim that all 420 PNGs are identical. The original automatic
comparison receipt correctly retains `manual_review_required`; its separate
hash-bound inspection record completes the coverage.

All 21 PDFs are byte-identical to the prior verified Azure factsheet baseline.
Both pages of each newly downloaded PDF were rendered at 120 dpi and displayed
individually: all 42 full-page inspections passed. HKD/HIBOR headings, graph
axes and legends, in-range 25-percentage-point histogram ticks, equal allocation
boxes, disclaimer spacing and footer bounds were checked. No clipping or
overlap was found. Native Microsoft PowerPoint rendering is still unverified.

The updated acceptance controller requires exact all-month coverage, all 126
workbooks, every downloaded-file identity, all slide preview hashes and explicit
inspection records for any permitted difference. Only slide 17 with the bounded
autofit-only difference can use this manual route. Missing/partial inspection,
changed text/geometry, unknown scaling, modified previews or changed reports
fail closed. Fifty-five targeted tests and 24 subtests pass; the single warning
is the deliberately duplicated ZIP entry in the rejection fixture.

Evidence under `.work/alpha-ms-presentation-worker-candidate-20261007-r5/runtime-v1/`:

- `candidate-qualified.json`: SHA-256
  `4a48850e6bbefbc75a099867bb8e70b13df65ac8015c9f87d051d06f005d79e9`.
- `cloud-local-audit-v1/receipt.json`: SHA-256
  `95af11df8f60221cc4602efdd838b7f27b64c3011f33e031c7cf8fc1abbc71aa`.
- `chart-workbook-acceptance.json`: all 126 bindings/cells verified.
- `cloud-visual-v1/receipt.json`: SHA-256
  `0782c79119292b7218f1f39e1f8440804b1e9894b99a81947c482d2f7d0cdc3c`.
- `conclusion-layout-comparison.json`, `cloud-slide-inspection.json` and
  `pdf-page-inspection.json`: bounded differences and explicit inspections.

Combined acceptance is recorded in `candidate-render-acceptance.json`, SHA-256
`831f2fef88261d37b84124090cc8b7c768b4d4e6193d4ddcc7b4edfda77a01a7`,
with status `isolated_cloud_render_and_output_acceptance_passed`. All six child
evidence records and the combined acceptance were retained create-only in the
existing protected Azure archive; exact-version readbacks passed for all seven.
The automatic difference receipt and rejected r2 outputs remain preserved.
These are private render qualification results, not production deployment or
owner report approval. The website editing feature remains outside this step.
Next: recovery/live-switch rollback qualification, then a scoped worker release,
private historical batch registration and website-download verification.

## Reporting fallback and isolated recovery, 8 October 2026

Installed-image fallback execution
`ibkrv2stg-month-end-factsheet-z63f6iz` succeeded for all 21 periods. Its
PostgreSQL transaction was repeatable-read and read-only. The downloaded
factsheets all match the accepted candidate PDFs byte-for-byte. The protected
`runtime-v1/rollback-qualified.json` receipt has SHA-256
`edddadf3d2f8d73db65e9b190c386e64e30b0480130b876cb94a6d6e7d1e2377`.
This establishes an installed-image factsheet fallback, not an old-image HKD
presentation fallback or a new independent PNG inspection. Visual equivalence
here follows exact PDF identity to the 42 previously inspected pages.

The temporary job `ibkrv2stg-ms-rollback-20261008` is manual-only, has zero
retries and uses a bounded import/file-identity check rather than either normal
reporting entrypoint. It borrows only the existing identity and Key Vault
references, without permission changes or secret-value reads. Its command does
not connect to the database. Both live jobs stay on the archived v14r13 image.
The temporary job was switched to the exact r5 digest; candidate check
`ibkrv2stg-ms-rollback-20261008-459gdho` succeeded. Archive restoration and the
restored-image check `ibkrv2stg-ms-rollback-20261008-hv1lixn` then succeeded.
Both image changes were checked against the protected actual baseline, with
only the documented empty-storage service projection normalized. The temporary
job has been removed and a read-only lookup confirms absence. Its definition
and evidence are recoverable from the protected archive; no live resource or
report was removed. The baseline archive readback took 4.325 seconds. This is
object-read timing, not full application RTO or an end-to-end database recovery
measurement.

Two service-projection checks initially rejected the operation and remain
preserved. Resource creation supplied empty registry username/password-reference
and ephemeral-storage defaults, and omitted an empty value beside an existing
database secret reference. After the image-only PATCH, only empty
`resources.ephemeralStorage` was omitted. Read-only comparisons proved these
exact differences. Versioned, protected controller continuations bind the old
and new controller sources, original plan and actual baseline. The PATCH
comparison ignores only empty ephemeral storage; nonempty values, credentials,
identity, schedules, commands and all other non-image settings still fail on
change. Original plans, baseline bytes, intents and rejected results were not
overwritten or relabelled as passing.

The actual job update uses the documented Azure Jobs JSON Merge Patch API:
https://learn.microsoft.com/en-us/rest/api/resource-manager/containerapps/jobs/update?view=rest-resource-manager-containerapps-2024-03-01
The controller checks drift before mutation and verifies settings afterward;
it does not claim an ETag-backed atomic concurrency guarantee.

Regression: 87 tests and 21 subtests passed. One installed-image manifest test
was skipped locally because the manifest exists only inside the isolated
candidate image; installed file identities and imports were checked in the
actual temporary candidate execution. The single ZIP warning comes from the
intentional duplicate-entry rejection fixture. Python compilation passed.

The acceptance binder checks exact January 2025-September 2026 coverage without
duplicate rows, the protected candidate manifest identity, unchanged source and
non-release flags, all 21 downloaded fallback PDF hashes, scratch removal and
actual archive recovery. Mutated candidate manifests, changed source flags,
report-release claims and a mislabelled production rollback fail closed.
This qualification does not establish full database/application disaster
recovery, native Microsoft PowerPoint rendering or a production image-switch
rollback. The next release must still guard concurrent live executions, capture
current live settings and verify the deployed image. If restoring the old image,
withhold new HKD MS presentation registration; only its factsheet fallback has
been qualified. Owner report approval, onboarding and website changes remain
separate.

Final isolated recovery receipt:
`rollback-rehearsal-v1/acceptance.json`, status
`isolated_image_switch_and_exact_archive_restore_passed`, SHA-256
`55a80d8ec34001a96b7ca2012b64b155204bb49cb5c7e7c119b597c28ba719d5`.
The before-state definitions for both live workers were recovered from the
exact protected plan version and matched; that object readback took 3.805
seconds. The isolated operation evidence and acceptance have their own
create-only protected archive receipts with verified exact-version readbacks.

The combined `runtime-v1/recovery-acceptance.json` now has status
`reporting_fallback_and_isolated_recovery_qualified`, SHA-256
`6fe3766d84aa2df4554f58555e5bd10f61f2b831fbfcf8050454994ad335b4bb`.
The binder recovered all three exact protected child versions, compared them
to the local records, rechecked both live jobs, then retained and read back its
own combined receipt. All 21 fallback PDFs match the accepted bytes. Zero
database writes, unchanged IBKR source data, no report release, no onboarding,
no website-download changes and no production promotion remain explicit.

This bounded recovery step is complete. Next is the scoped reporting-worker
release, followed by a new private 21-factsheet/21-presentation historical batch
and verified website downloads. Full application/database recovery and an
actual production rollback are not represented as complete by these receipts.

## Scoped reporting-worker release, 8 October 2026

Owner-authorized release is limited to `ibkrv2stg-onboard-render` and
`ibkrv2stg-month-end-factsheet`. The image comes from the frozen, scanned r5
context, not the dirty working tree. No website or other workload is a target.
The manual worker is updated and its installed check
`ibkrv2stg-onboard-render-e8pjmuw` passed. The scheduled worker update and
verification also passed, followed by its installed check
`ibkrv2stg-month-end-factsheet-55fs6rc`. Both checks verified all 41 files.
Both persisted images now use
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:d98e16415d7e4b601571913f2d3be3bc48e62c69d9725419f556a2b71333a584`.
Their original rollback image remains
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:f7aaa6935794c7a2ce1ef94ed177fa3f987fd31df8df7d6b56f644242922a590`.

The protected `deployment-v1/plan.json` has SHA-256
`78f4224ba3f7f9886f099a7bf1b99a028fbaff02533708bad2a7d9e333230b44`.
It captures both actual ARM definitions, the rollback image, immutable release
and recovery identities, and the frozen deployment controller SHA-256
`d720f0caef7ab580482e11fb7acc5a24566ffaa77e057d8d6ddfedb5e9776fa3`.
Exact protected plan and mutation-intent readbacks are required. The Azure CLI
and raw ARM metadata have different serialization; raw ARM metadata matches
the prior protected ARM baseline. That comparison, not a cross-schema hash,
governs the new release.

Only image changes are PATCHed. Identity, configuration, secret references,
normal commands, environment and CPU/memory remain outside the authorized
change. Empty secret-bound values and empty ephemeral storage have the narrowly
qualified Azure representation normalization; no nonempty value or different
credential is ignored. The scheduled job's existing cron is `0 9 * * *`, daily
at 09:00 UTC, not a monthly-only schedule. The controller refuses the
08:00-09:30 UTC release window and active/unknown executions, including subsequent
execution-list pages. Manual-worker verification must pass before scheduled
worker promotion. No execution is cancelled or replaced.

The initial manual-worker transient check was rejected with HTTP 400. Its
protected original intent remains intact, without a dispatch acknowledgement
or matching Azure execution. Provider validation details were not available;
do not assert whether null fields or command size alone caused the rejection.
The separate `installed-check-v2` controller omits null template fields and
empty secret-bound values and compresses the identical frozen source, following
the already successful transient-probe request shape. A compressed-source replay
test proves exact equality. The corrected manual execution succeeded; the
original request is not relabelled as successful.

Each installed check verifies 41 files: inherited HKD/HIBOR/calendar and
factsheet code, the r5 presentation changes, both release manifests and the
approved platform template. Both normal entrypoint modules and their shared
presentation validator are imported, without calling either entrypoint. These
checks neither process reports nor connect to the database. All 21-month numeric
and visual acceptance records remain unchanged; a new private historical batch
has not yet been generated or registered.

Regression: 109 tests and 38 subtests passed; one installed-image-only test was
skipped locally. The intentional duplicate-ZIP rejection fixture produces the
single warning. Controller compilation passed. Mutation and dispatch attempts
are one-shot: uncertain outcomes must be reconciled, not automatically retried.

Deployment completion is now protected and readback-verified in
`deployment-v1/completion.json`, status
`scoped_reporting_workers_deployed_and_identity_verified`, SHA-256
`9ab791234daee6b1e62e081466c91da354e0d3132044b8fd475d257938210627`.
Both installed checks have separate protected readback-verified receipts:

- Manual worker qualification: SHA-256
  `2ca22d91c33ee8ccc2bf46dc2f768386816e08a45565315a4d2d76b83c1d4d11`.
- Scheduled worker qualification: SHA-256
  `441187f29218faa0667cd8a14e218dd82b0a13e776b95c7adfe85387a17480cd`.

This worker-image deployment step is complete. Normal processing was not invoked
by these checks; no new historical batch was generated. Zero database connections
or writes, unchanged IBKR source data, no report release, no onboarding activation
and no website batch registration are explicit in the completion receipt.
Schedules, identity and credential references are preserved. The old-image
factsheet fallback and protected rollback definitions remain available, but an
actual production rollback is still not represented as exercised.

Next: generate and register the new private 21-factsheet/21-presentation batch
through the installed worker, verify its outputs and authenticated website
downloads, then separately obtain report approval and consider onboarding.

## Installed-worker private historical batch, 8 October 2026

Live checks reconfirmed both deployed r5 image digests, preserved settings,
idle execution inventories and the exact protected deployment completion.
The one-shot installed-image execution
`ibkrv2stg-month-end-factsheet-kcgrx3c` succeeded. Its two existing probes enforce
PostgreSQL repeatable-read READ ONLY; the normal onboarding queue consumer was
not invoked. All 21 immutable HKD snapshots and current metrics were checked,
then 21 PDFs and 21 PPTX files were rendered into a new private prefix and
downloaded with exact SHA-256 readback. No registration occurred in that run.

The protected `registered-batch-v1/render-qualified.json` has SHA-256
`11c45b6a94c151a31021baa7ad28dc0cb7be502307a35de2e4f32d0737ffba82`.
All 21 PDFs are byte-identical to the accepted cloud baseline. New PPTX
package members are not byte-identical, so no ZIP-byte equivalence claim is
made. Independent frozen-candidate auditors passed all 126 financial charts,
147 risk-metric values and 126 embedded workbooks. Their receipt has SHA-256
`ad98690d1e61cdbc8f704e9e0a867c1c819643e6cb3f5c5ae7596fd2bba8d1ce`.

All 420 slides and 42 PDF pages were rendered again. Every preview is
pixel-identical to the previously accepted and individually inspected cloud
reference, whose recorded preview hashes were rechecked. The comparison receipt
has SHA-256
`6ce3cf0704334b128aad2a4f2ec2e8a20bf2f364a4589bbb899088c99f5a8094`.
The latest September PDF pages and presentation conclusion slide were also
displayed and inspected in this turn. Native Microsoft PowerPoint interaction
is still not represented as tested.

Registration is a separate metadata-only transaction, with a protected
read-only baseline required first. Original baseline execution
`ibkrv2stg-month-end-factsheet-gbng4zr` failed with a sanitized `TypeError`
marker. Direct log access timed out; the Azure Monitor marker was read instead.
Local reproduction showed that `canonical_json` does not serialize PostgreSQL
UUID scalars. A version-2 runtime explicitly normalizes UUIDs without broad
fallback serialization. The original runtime, controller, intents, execution
and failure are retained; no mutation phase was dispatched for that attempt.
The corrected runtime/controller use new plan, phase and result names.
46 safeguard tests and compilation passed. The first test invocation used an
incorrect test module name and an unavailable sandbox temporary directory;
the corrected invocation used the workspace temporary directory and real modules.

At this checkpoint the installed render and output acceptance are complete,
but website batch registration and authenticated website downloads are pending.
IBKR sources, source snapshots, previous report files, approvals and onboarding
lifecycle remain unchanged.

The corrected read-only baseline execution
`ibkrv2stg-month-end-factsheet-xdo8i1f` subsequently passed. Its protected
qualification SHA-256 is
`027b8bedfd56af08169e88bb42b64995f0b49c22f185a33e7029937931abe08c`;
the baseline state SHA-256 is
`7f1407e986353b9ab0c8bf232602748c5a661c5b0320def01dce6b51facd5e0c`.
It captures 12 prior batches, 486 prior report items and 366 generation runs,
plus templates, approvals, subject/case state, immutable snapshot identities
and release records. Large manifest/validation JSON is fingerprinted, retaining
exact content checks while keeping protected recovery evidence below its bound.
UUID normalization is explicit; unsupported types still fail closed.

Registration execution `ibkrv2stg-month-end-factsheet-3zkkl97` succeeded in one
serializable transaction. New private batch
`86e6c5fe-bc53-40f2-bd53-aad8ff0be84f` contains 42 validated items and 42 newly
inserted generation runs, with `awaiting_owner_approval` batch status and
`awaiting_approval` run statuses. Content-addressed private files are create-only
and readback-verified. Per-batch template-version suffixes prevent revival or
overwrite of an older generation run.

Only old unapproved batch `ef94a9e0-4902-4fdd-a1e4-8faff5f57030` changes status
to `superseded`. Its manifest, items, generation records and stored files remain.
The transaction checks exact prior-state preservation before committing. An
audit event records the owner-authorized private registration. No normal queue
consumer, calculation refresh, source-data write, report approval, release or
onboarding activation runs. No account/case stage or configuration changes.

The protected registration qualification has SHA-256
`23af81c06c1877ca732ea67d4c6a5207f4bc628aaa878039d44c736a28fb8522`.
New manifest SHA-256 is
`688297133906a87bc591a6d58b7a7aa07dda7b20c05aac3f5248d5e46f939705`.
Final independent read-only registration/42-file verification remains pending
at this checkpoint, followed by authenticated website downloads.

Final read-only execution `ibkrv2stg-month-end-factsheet-kh8v62w` passed.
Its protected qualification SHA-256 is
`1102dd5a5b7e80a505abccd0af535c958458397c644e4d4dc003aca8b2f8968a`.
It independently compared the exact protected prior state, rechecked all 42
registered blob contents and byte counts, and passed all six historical-batch
validation checks. The canonical manifest-content fingerprint equals the
registered manifest SHA-256. The batch is ready for owner review, not approved.
IBKR source/snapshot state, prior generation records/items, templates, case
configuration, approvals, releases and lifecycle remain preserved.

The combined acceptance is protected and exact-version readback-verified under
`registered-batch-v1/completion.json`, status
`installed_private_historical_batch_registered_and_verified`, SHA-256
`34b8036a2647858ac8f2463378c498333511e61e527f980ae76020c886cadcc0`.
It binds render, independent numerical/workbook audit, all-preview pixel
identity, protected baseline and both registration/verification phases.
Both reporting-worker images and non-image settings were rechecked unchanged.
Authenticated website download identity
verification and native Microsoft PowerPoint interaction remain separate open
checks. Report approval, release and onboarding activation were not performed.

## Authenticated website download identity, 8 October 2026

Completed the next read-only gate in the existing Chrome owner session.
The historical-report page was refreshed after registration; it displayed
the new manifest prefix `688297133906a87b`, 21 completed months, 42/42 validated
files and all six historical-batch checks passing. The initial open tab still
displayed the superseded batch until refresh; no regeneration was requested.

All 42 visible private-review links were downloaded through the documented
browser `downloadMedia` operation, generation-run IDs 838 through 879.
The actual browser-returned filenames and route IDs were recorded in
`registered-batch-v1/website-downloads-v1/browser-observation.json`.
Every downloaded file matched the accepted registration proof's SHA-256,
byte count, filename, period, report type and batch identity. Total downloaded
bytes: 32,814,708. This verifies the website delivery of the accepted artifacts,
not merely their underlying storage copies. Fifteen local safeguard tests
passed; the checker and tests compiled successfully.

Acceptance is recorded separately, preserving the previous completion:
`registered-batch-v1/website-downloads-v1/acceptance.json`, status
`authenticated_website_download_identity_passed`, SHA-256
`9a982281097f12f2ebfe5250d66d2ee15c197cbac4c81366700ea89226003acd`.
The prior completion and registered qualification were independently restored
from their exact protected archive versions before acceptance was sealed.
The sanitized acceptance archive has SHA-256
`df80a87e8f7bc45b6763922be1465cd8e82b58f040c4235e409fe570798444e4`,
version `2026-10-08T01:19:27.4673621Z`, and exact-version readback verified.

The approval button remained disabled and the batch remained Awaiting Owner
Approval. No approval, release, activation, source-data write, report edit,
renderer deployment or authentication/configuration change was performed.
No cookies or tokens were retained. Individual HTTP response headers and
negative authorization tests were not independently captured in this check;
they are not claimed as newly qualified. Native Microsoft PowerPoint opening
and editing interaction remains untested and is the next bounded check before
separately requesting owner report approval and private website-preview review.

## Native PowerPoint checkpoint, 8 October 2026

Prepared 21 disposable presentation copies in
`registered-batch-v1/native-powerpoint-v1/copies/`. Each source download and
each copy matched the accepted SHA-256 before native inspection. No accepted
download, cloud artifact, renderer, source data or report status was edited.

Opened the September copy through Microsoft PowerPoint's local file picker
using the computer-use skill. PowerPoint displayed the cover and `Slide 1 of
20`, without a repair prompt. The document opened in Protected View and
displayed `Enable Editing`. The agent did not click it, remove the file's
Internet-origin marker, change trust settings, or bypass the protection.
The computer-use skill's security restrictions require user handling of
this file-trust action before native editing tests can continue.

`native-powerpoint-v1/checkpoint.json` records this partial result. Native
chart/table editing and all-month native-open checks remain pending; the
existing all-month package, numerical, preview and website-download acceptance
is not a substitute for those interactions. The September test copy remains
open for the user. No report approval, release or onboarding activation occurred.

## Native editability failure reproduced, 8 October 2026

On resumption, the September test copy was already in normal PowerPoint editing
mode. The agent did not enable editing or change trust settings. Slide 5's
native table header was changed from `Rank` to `Test`, visibly checked, and
undone. The restored `Rank` header was also confirmed in the saved copy.

Slide 6 was exposed as a native clustered-bar chart, and its `Edit Data`
operation opened an embedded Excel data grid. Without changing any data cell,
the chart's percentage labels became raw decimals. Saving only the disposable
copy reproduced persistent format loss: the numeric cache format changed from
`0.0%` to `General`, and the value-axis format changed from `0%` to `General`.
All six charts retained numerically identical cached values when parsed as
float (maximum absolute delta zero). This is a display-format preservation
failure, not a newly observed financial calculation mismatch.

Read-only inspection found that all 126 embedded chart workbooks in the 21
accepted presentations lack an `xl/styles.xml` part. The likely cause is the
interaction of missing workbook numeric styles with source-linked chart number
formats. Only slide 6 has this native reproduction so far; the other chart
families must not be described as natively interaction-qualified. The original
authored-package verifier also rejects Office's rewritten numeric serialization
under its strict 15-digit rule; that rejection is recorded, not silently treated
as an analytics error or a passing saved-package qualification.

The Excel mini-editor was not separately targetable through the returned window
inventory. No fabricated window handle or alternate UI automation was used.
Saving and closing the disposable presentation through PowerPoint closed the
dependent editor. The saved failure copy is preserved with SHA-256
`6d5334bc7ab0c846d52f3db7aa8afe516a36ecf428a3657a26cb8e3fc4eda769`.
All 42 accepted website downloads were rehashed and remain unchanged.

`native-powerpoint-v1/editability-failure.json` records the failed gate.
No renderer code or Azure deployment was changed during this inspection.
Next is number-format preservation correction and native edit/save/reopen
qualification, before separately requesting owner report approval. All-month
native opening, six-chart native editing, release and onboarding remain open.

## Number-format correction and representative roundtrip, 8 October 2026

Updated the application chart-workbook postprocessor to emit explicit numeric
cell styles, stylesheet relationships and content types. Chart axis formats
are explicitly unlinked from source formatting; data-label formats are set
without changing numeric values, categories, signed rich labels or slide
object geometry. Existing valid bound workbooks remain byte-for-byte preserved
by default. An explicit, fail-closed upgrade handles only our independently
verified literal snapshot workbooks, never arbitrary source formulas or edits.

Created new private candidates for all 21 months under
`.work/alpha-ms-number-formats-20261008-v1/`. All 126 chart/workbook pairs and
147 risk metrics passed the financial audit. Only six chart parts and their
six embedded workbooks changed per presentation. All 42 accepted downloads
were rehashed unchanged. Package/financial receipt SHA-256:
`15cd7fa2fae9965a4a1fa29abdff7fef769d7a782b4079a2f5c065a0c6a69701`.

Opened a separately generated disposable September copy in normal PowerPoint
editing mode, with no security-setting change or agent `Enable Editing` action.
Slide 6's embedded data editor retained percentage formatting. Resizing the
chart worked; its original size was restored with Undo. Saved and closed the
copy through PowerPoint, reopened it, and inspected slide 6 again. Its labels
still show percentages (including `35.6%`), with a `0%` to `40%` value axis and
no repair prompt. The saved copy SHA-256 is
`7afcf3335668e4cb877ca1cd2e6fb3cdd7fcf8689375cfc3ad427bbe8cf5b951`.

A separate read-only Office-roundtrip checker validates all six saved charts'
values, categories, cell formats, axes, effective labels and rich signed text.
It allows Office's legitimate serialization changes and removal of redundant
group formatting, without loosening the authored-snapshot validator. An initial
exact-XML comparison rejected redundant-label cleanup; inspection established
that the effective formats were unchanged, and negative tests now separately
reject value changes, lost workbook formats and altered axis precision.
The saved-package receipt SHA-256 is
`e43f5f05e2829b31067ebffa0195744aa3c07e010fdea9bde1bee8e023bb7c26`.

Regression: 64 tests run, one skipped, no failures or errors. Two initially
guessed test-module names were not present; the correctly named suite was then
run successfully. The read-only preview helper also needed current slide-index
handling: its first run produced an incorrectly named preview, preserved as
partial evidence; the corrected new-folder run produced 20 uniquely numbered
slides. Sixteen September PNGs exactly match the prior visual reference. Slides
6, 9, 10 and 11 were compared at full size: explicit formats change automatic
label padding/plot bounds, with slide 11's preview top tick increasing from HKD
60 to HKD 70. Values, units, signs and slide frames remain correct. This is not
all-month visual reacceptance, and no pixel-identical claim is made for these
four charts. The original failed native copy and receipts remain preserved.

`native-ui-check.json` records the representative passing interaction, not
completion of all six chart-editor tests or all 21 native-open checks.
All-month visual qualification, remaining native interactions and scoped Azure
release qualification are still open. No Azure deployment, report approval,
historical replacement, release, onboarding activation or IBKR source write
occurred in this step.

## Six chart-family editor checks and all-month format review, 8 October 2026

Completed the remaining representative September native chart-editor checks
on a disposable copy: performance index (slide 7), allocation (8), signed
component contributions (9), component-type contributions (10), and HKD cash
(11). Slide 6's earlier percentage-editor and resize/Undo check remains bound
to its preserved evidence. Each chart's own editor was inspected, rather than
mistaking the still-open previous chart editor for the selected chart's data.
PowerPoint save/close reset the dependent Excel mini-editor between checks.
The editor remains not separately targetable, so no direct cell-value mutation
or editor-window resize was attempted. Only disposable generated copies changed.

The all-six saved-copy checker passed all values, categories, cell formats,
axes and signed chart labels. That v1 saved copy has SHA-256
`465b54410b71a8748041fde627374f07a88cd46c2f554c2b63c8ccbdfcf25d1e`.
Native inspection found one further usability defect: the HKD workbook's
default-width numeric column displayed `####` instead of its correctly
formatted value. The chart itself and its numeric value remained correct.

Updated only the shared HKD snapshot worksheet's numeric column B to width 16.
New private candidates under `.work/alpha-ms-number-formats-20261008-v2/`
change only slide 11's embedded cash worksheet relative to v1; its values,
headers, categories, charts and other workbook/slide parts are unchanged.
The authored validator requires the HKD width for new format qualification,
while default preservation still accepts valid older bound packages unchanged.
The independent Office checker also rejects a lost or hidden cash column.

Opened the new September candidate in PowerPoint, confirmed the editor shows
`HKD 58.00`, saved/closed/reopened it, and confirmed both chart and editor remain
correct without a repair prompt or trust/security-setting changes. The final
reopen's automatic in-memory refresh was discarded, preserving the verified
saved copy with SHA-256
`6a07c77a23b0456658c0ad0924c7a26b99a5d4e392d3cdc5f8fbec65101226e3`.
The v2 saved-package checker again passed all six charts. Representative
non-cash editor checks carry forward through byte-identical non-cash parts,
not through an unsupported claim that all six v2 editors were reopened.

All 21 private v2 presentations passed independent financial qualification:
126 financial chart/workbook pairs and 147 risk metrics. The read-only preview
runner rendered 420 slides. Every v2 PNG is identical to its v1 counterpart,
so the cash editor correction has no slide appearance change. Compared with
the previously accepted cloud visual reference, 336 slides are hash-identical
and their earlier acceptance carries forward. All 84 changed charts (slides
6, 9, 10 and 11 for each month) were individually inspected at full 960x540
size in this turn. Labels, signs, units, axes, tables and fit passed this scoped
format-delta review. Automatic plot padding/bounds changes from the earlier
number-format correction remain visible and accurately recorded; no claim is
made that these 84 charts are identical to the older cloud previews.

The binding utility rechecked every candidate/preview/reference hash, the exact
21-month coverage, cash-worksheet-only delta and all 42 original website
downloads unchanged. `review-acceptance.json` has status
`private_all_month_format_financial_and_visual_delta_review_passed`, SHA-256
`a217a02c02177fe9f7ab54026aff13829974d25877f541e4657acedb17ac9aa9`.
Candidate financial proof SHA-256:
`d627ffe97cd76c0158990d5bc3d67f41e1f5310b6f164d2ffc44a4b616b55dbc`.
The evidence explicitly keeps all-month native opening and new Azure release
qualification false. It is not owner approval or release authorization.

Regression: 69 tests run, 68 passed, one skipped, no failures/errors. Negative
tests reject missing HKD width in new qualification and loss after Office
roundtrip. Compilation and repository whitespace checks also passed.

Next: open the 21 updated monthly copies in native PowerPoint, then freeze and
qualify a fresh scoped Azure candidate for these renderer changes. No Azure
resource/deployment, website report replacement, report approval, release,
onboarding activation or IBKR source-data write occurred in this turn.

## Owner-reported all-month PowerPoint opening, 8 October 2026

The owner replied `done` to the next step "open every monthly presentation in
PowerPoint." Recorded the requested 21-month opening check as completed by the
owner in `owner-native-open-confirmation.json`, separately from the immutable
prior qualification receipts. The current candidate/review receipt hashes were
rechecked and match the recorded v2 private batch. Actual opened file hashes,
repair-prompt outcomes and all-month edit/save behaviour were not independently
observed or explicitly reported, and are not claimed as verified here.

Next is a fresh, reporting-only Azure candidate carrying the qualified format
and HKD editor-width changes: isolate its source delta, run regression and
runtime tests, scan the exact image, and qualify rollback before cutover. The
older workbook candidate preparer is bound to its original parent/delta and
must not be reused blindly for these new changes. No Azure build, deployment,
report approval, report replacement, onboarding activation or source-data write
was performed in response to this confirmation.

## Scoped format-worker qualification, 8 October 2026

Read-only live checks confirmed both `ibkrv2stg-onboard-render` and
`ibkrv2stg-month-end-factsheet` still use the installed reporting digest
`sha256:d98e16415d7e4b601571913f2d3be3bc48e62c69d9725419f556a2b71333a584`.
The new create-only candidate is
`.work/alpha-ms-format-worker-candidate-20261008-r4` and inherits that exact
image, rather than the older r5 candidate's upstream base. It copies the verified
r5 context and changes only workbook formats/width, strict MS shared validation,
the related tests and read-only Office roundtrip checker. All other renderer,
worker and design code remains the frozen r5 version. No unrelated website or
onboarding feature is included. Parent build provenance and the private review
are bound in the candidate intent.

The new shared MS gate calls workbook validation with `require_format=True`.
Negative tests cover legacy unstyled snapshots and missing HKD editor width.
Legacy read-only verification/materialization can still preserve old packages;
it does not qualify them for new report registration.

The isolated runtime contains only hash-verified inherited application sources
and the frozen candidate overlay, with no repository import fallback. Tests use
a private workspace temporary directory. All **104 tests passed**, with no
skips or errors. Historical formatting upgrade replay reproduced all **21**
reviewed v2 presentations **byte for byte** and passed the shared gate for
**126 charts and 147 risk metrics**. The accepted 42 download files were
read-only inputs. This is historical upgrade replay, not a new Azure render.

Frozen source intent SHA-256:
`f9696a605af16a9db897b0abcaa146a2620f24b5bf6bd47b79d5deff37843d95`.
Isolated local qualification SHA-256:
`11f6c7153746db58d68d86d63e6cfc4f5b3a8daf87ddc9965d5182c6e22c8fe1`.

Fresh full-render qualification then stopped before creating an output:
the local Artifact Tool 2.8.89 import does not resolve the unchanged renderer's
`tb/fql4fa5s` table ID. The approved template hash is unchanged. The failure and
local library identity are recorded in `fresh-render-stopped.json`. This is an
observed local compatibility failure, not evidence that production rendering
failed. No structure receipt, fresh-smoke pass, image-build acknowledgement,
security acceptance or deployment is claimed for this new candidate.

Earlier attempts r1-r3 are preserved: inherited private render outputs required
excluding only known `.work/presentation-validation/` evidence when checking
the parent runtime; tests required a workspace temporary directory and the new
base-image pin; an attempted comparison used the older r2 cloud serialization
instead of the accepted r5 downloads. Candidate r4 corrected those qualification
setup errors without changing report values or weakening the format checks.

Next: qualify fresh rendering using the exact installed presentation runtime,
or separately qualify a compatible renderer. Then complete all-month structure,
exact-image build/security/runtime and rollback gates before production cutover.
Compilation and repository whitespace checks passed. Azure resources, website
reports, report approvals, MS onboarding and immutable IBKR data remain unchanged.

## Fresh desktop compatibility and Azure candidate build, 8 October 2026

The private fresh-render mismatch was investigated without changing the frozen
production renderer or bundled dependencies. Current Artifact Tool import
assigns different IDs to all eleven tables and six charts. Shapes and slides
retain their source identities. A desktop-only adapter binds tables by exact
owner, row/column count, preview and geometry; charts by owner, type, title,
name and geometry. Missing, ambiguous or changed elements fail closed.

One precise geometry interpretation difference is allowed: the approved slide
5 source OOXML contains an 810px outer table frame and explicit column widths
totalling 757.500157px; the newer importer exposes the latter. No position or
width is edited by the adapter. The newer chart schema also requires an explicit
`solid` style on legacy green/red point borders. The adapter adds that default
only for the exact existing width/color configurations, preserving values,
signs, category order and all other options. Ten compatibility tests pass.
These desktop helper files are excluded from the Azure build context.

The unchanged frozen renderer then created a fresh September PPTX and passed
the strict shared financial/workbook gate: six charts and seven metrics.
Nineteen PNG previews exactly match the reviewed private v2 batch. Slide 17
differs only in text wrapping: both 960x540 images were inspected at full size,
native text content was verified identical, and no clipping, missing text or
changed figures were observed. This is recorded as manual private visual-delta
acceptance, not an exact pixel match or owner release approval.

Fresh output SHA-256:
`49d5ecd6678fbf011e4423026026d0663afaf86fdf37148c1fa20fdf59f51eca`.
`fresh-smoke-qualified.json` SHA-256:
`f97da4fb13cebb6375810ff47cdb445559dc06182829a82e6f2adc83d919ec8f`.
`fresh-slide17-visual-review.json` SHA-256:
`4e7f285e32129c2a2df68c1163f16efee38da4b8cda6e2b84a684ea0eaab2b08`.
Fresh rendering used no desktop finalizer and is not Azure runtime evidence.

Independent integrity and geometry checks pass for the 21 historical outputs
and fresh smoke file: 22 documents, 44 inspection reports, no findings or new
unreviewed layout warnings. `local-structure-qualification.json` SHA-256:
`e46d59c7890b1a8a89208410fa5180ccee75804d18bfb82df7966bf579627d74`.

The qualified source context was submitted once to Azure Container Registry.
The command returned no build acknowledgement, so the existing native run was
reconciled rather than redispatched. Run `cka9` succeeded and uniquely matches
the frozen tag and dispatch timestamp. Candidate image digest:
`sha256:af5d255d4f71f762db763fa7bb8ab542e9c6f52a211c48b51cb1f44deb488fe9`.
No persistent reporting job, website, credential, source data, report approval
or onboarding setting was updated. This creates a candidate registry image
only; its native build-log, security, Azure rendering and rollback gates must
pass before installation and historical report replacement.

### Native build and exact-image security gate completed

Run `cka9` passed the report-runtime/native-chart checks, the shared worker
contract check, and all 104 frozen tests in Linux. Native build qualification
receipt SHA-256:
`72126530e9c1f5145648baafd5eb1e428dc9766e496f2aa10c1107440c124ed8`.

The exact `af5d255...` image was scanned with qualified Trivy 0.75.0 after
verifying 53 registry content blobs. The vulnerability/SBOM assessment passes:
0 critical, 0 high, 0 unknown, 40 medium and 19 low findings; **0 blocking
findings**. Medium/low findings remain present and recorded; this is not a
zero-vulnerability claim. All 99 scanned packages are reconciled with the
software inventory (100 components), covering Debian, Node and Python.

Build/source receipts, raw vulnerability output, software inventory and the
assessment were retained create-only in the existing protected archive and
read back by exact version/hash. Scan receipt SHA-256:
`b4217ad54dd8f6ba22237e62bedde5e7a519c42d193490f55d2eaf3ab0a42e2a`.
Protected-readback receipt SHA-256:
`ffd4fde9aa7aafb6fa9f6409ad178b53f04730ba42582c91d01746831389b455`.
Both persistent reporting job images and settings were rechecked unchanged.

The build/security milestone is sealed in
`build-security-phase-qualified.json`; full cloud render and rollback remain
explicitly false. The next read-only runtime controller now accepts the exact
format-candidate naming pattern and assigns a separate create-only
`review/alpha-world-ms/format-worker-r4-20261008/` prefix. Presentation-candidate
prefixes are unchanged. Invalid dates, kinds, revision names and namespace
reuse are rejected. Eighteen controller/build safety tests pass. No live
runtime probe has been prepared or dispatched in this continuation.

Next: read-only all-month rendering from this exact candidate image, independent
numerical/visual checks on the Azure outputs, and the candidate-specific
rollback rehearsal. Installation and historical report replacement are not
performed until those separate gates pass. Existing released reports, IBKR
sources, report approvals and MS onboarding remain unchanged.
