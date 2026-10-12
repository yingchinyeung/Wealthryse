# WealthRyse Portfolio Data Platform AI agent handover

Prepared on 11 October 2026 for the owner and the successor AI agent. Private operational document. It contains infrastructure identifiers and internal evidence locations, but no passwords, tokens, connection strings or signed download URLs. Keep it within the owner's authorized project environment.

## 1 Start here

The full upgrade is **not complete**. The last sealed checkpoint is stage 44, recorded at 20:04 UTC on 11 October 2026. The latest website reporting-handover correction and additive database migration are deployed. Corrected historical reporting inputs and a private report batch have been qualified in isolation, but their governed production migration and replacement release remain unfinished. Private-network cutover, credential rotation and several acceptance gates remain unfinished.

The **14-day observation and real viewer sign-in remain UNVERIFIED**. The owner explicitly requires these labels until the checks actually complete. An owner login, automated authorization tests, manual job runs or historical burn-in records cannot substitute for them.

This document was prepared from local source, sealed checkpoint files, the chronological progress record and the conversation's owner decisions. No fresh Azure verification or Azure mutation was performed to prepare it. Treat infrastructure and release status below as last-observed state, not a promise that another operator has not changed it. Start with a read-only drift check before taking ownership of deployments.

Project root, called `ROOT` throughout this document:

```text
C:\Users\antho\Downloads\IBKR Data\IBKR Portfolio Platform v2
```

Paths beginning with `docs/`, `scripts/`, `src/`, `tests/`, `migrations/` or `.work/` are relative to ROOT. The parent workspace is `C:\Users\antho\Downloads\IBKR Data`. The successor needs access to this workspace and its private Azure evidence; this handover alone is not a replacement for the protected artifacts.

Read these in order:

1. This document, especially sections 2, 8, 10 and 11.
2. `.work/platform-upgrade-20261011-stage44/closeout.json` and its protected receipt.
3. `docs/AI_AGENT_HANDOVER_2026-10-11_inventory.json`, the accompanying exact 35-workload image and invariant inventory extracted from that checkpoint.
4. `.work/platform-upgrade-20261009-v1/deployment-progress.html`, newest section first. It is the detailed chronological journal, including failed attempts and links to evidence.
5. The workstream-specific evidence and controllers in section 12 before planning any mutation.

Do not resume an obsolete checkpoint automatically. Older README entries, the original release-gate ledger and early MS/T onboarding notes describe historical states, sometimes three accounts or MS being paused. They do not override stage 44.

## 2 Current status and immediate priorities

| Workstream | Last evidenced status | What remains |
| --- | --- | --- |
| Website and reporting-handover fix | Deployed; healthy revision; anonymous checks denied | Wider authenticated acceptance and production replacement workflow |
| Migration 062 | Applied; additive schema; role permissions preserved | Do not apply again or drop it during application rollback |
| Onboarding, month-end and snapshot reporting runtimes | Corrected immutable images deployed | Governed migration of historical inputs and report registration/release |
| Historical report batch | 105 PDFs and 105 editable 20-slide decks generated privately | Complete historical visual acceptance, production registration, fresh approval and download checks |
| Historical financial-input corrections | Isolated QA versions and rollback rehearsals completed | Fresh production reconciliation and scoped versioned migration |
| SKYD versus PSKY | Specific dated broker-symbol alias reconciled; consumer fixes deployed | Do not infer verified 2008 security continuity or independent price certification |
| Runtime security | Tracked old image families replaced or explicitly retired; scoped scans passed blocking thresholds | Medium/low findings remain; fresh scans and continuing maintenance |
| Backend private networking | Endpoints, admin/build path and reversible DNS/caller trials exist | All-caller platform-secret refresh, cold pull, negative-external tests and final public-access cutoff |
| Credentials | Metadata inventory, role grouping and monitoring established | Coordinated rotation, recovery and reconnection tests |
| Recovery | Same-region clones, transaction/status rollback and legacy HA/failover rehearsal | Quantified owner recovery objectives and final end-to-end recovery acceptance |
| Whole scheduled cycle | UNVERIFIED | Observe genuine dependency-ordered normal processing |
| Fourteen-day observation | UNVERIFIED | Actual eligible elapsed success period without hiding failures |
| Real viewer sign-in | UNVERIFIED | Existing viewer must actually sign in when available |
| Human alert receipt | UNVERIFIED | Provider success and a fired Azure alert do not prove inbox receipt |
| Independent crisis-price certification | UNVERIFIED | Independent historical adjusted-price evidence is still unavailable |

Recommended next implementation work is a fresh baseline followed by the governed production snapshot/report correction. Do not begin with another blanket deployment: the latest runtimes are already installed. Network/credential work can be planned separately, but serialize live changes and preserve rollback paths.

## 3 System purpose and architecture

The platform provides private portfolio dashboards, controlled data exports, deterministic calculations, risk analytics, monthly PDF factsheets and editable PowerPoint presentations. Entra authentication, tenant boundaries, account/subject entitlements, review decisions and immutable artifact hashes constrain access. It is not an unrestricted public report website.

Five portfolio subjects appear in the current catalogue:

| Subject ID | Role | Reporting convention |
| --- | --- | --- |
| `U16088571` | Physical IBKR account, often described as the US portfolio | USD |
| `U16088606` | Physical IBKR account, associated with Greater China | USD reports; underlying holdings can have mixed native currencies |
| `hong-kong-china` | Filtered/derived portfolio | USD reports; not an independently traded IBKR account |
| `alpha-world-ms` | Composite/model portfolio | HKD |
| `alpha-world-t` | Composite/model portfolio including Taiwan | HKD |

Resolve precise legal/display names and current composition from registered definitions and frozen evidence, not this shorthand. A derived portfolio must not be assigned invented IBKR executions or a fake broker account number.

The principal processing chain is broker/source ingestion, reconciliation, benchmark/FX/HIBOR enrichment, derived calculations, risk/cache generation, assurance, immutable reporting snapshots, rendering, private registration and governed release. Schedules and dependencies must be preserved. An isolated passing command is not proof that the full scheduled chain operates.

The website's database role is read-only. A separate narrowly controlled approval identity handles authorized release writes. Downloads are streamed through access checks; internal blob names and hashes are not meant to become friendly filename suffixes. Never expose storage credentials or signed URLs in ordinary output.

Core code is under `src/ibkr_etl/`; relevant modules include `web.py`, `monthly_reports.py` and `monthly_release.py`. The workspace also contains newer or unqualified features absent from installed source. Build from each consumer's verified installed base and a reviewed delta, not indiscriminately from the entire working directory. Web/reporting builds require their appropriate renderer dependencies; an ingestion-only image is not interchangeable.

## 4 Infrastructure and operational locations

| Item | Identifier |
| --- | --- |
| Azure subscription | `3956e821-cee4-403f-8708-e84e2ed490eb` |
| Resource group | `rg-ibkr-database-v11` |
| Data location authorized by owner | East Asia |
| Main registry and storage account name | `ibkrv2stgauhecwc4itaoo` |
| Legacy registry and storage account name | `ibkrv115rcc62xxqblay` |
| Main vault | `ibkrv2stg-auhecwc4itaoo` |
| Flex vault | `ibkrv2flexauhecwc4ita` |
| Legacy vault | `ibkrv11-5rcc62xxqblay` |
| Log Analytics workspace ID | `5264a124-10cd-45f6-93a5-6d65c37e9bfb` |
| Main production PostgreSQL host | `ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com` |
| Production database | `ibkr_staging` |
| Website read-only role | `ibkr_stg_web` |
| Separate approval role | `ibkr_stg_approval` |
| Existing administrative role | `ibkrstgadmin`; do not use for routine website reads |
| Private recovery PostgreSQL host | `ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com` |
| Recovery host private IP at checkpoint | `10.50.11.4` |
| Original QA recovery database | `upgrade_g04_20261010` |
| Migration/test QA database | `upgrade_g04_migration_20261011` |
| Application tenant used in qualified evidence | `00000000-0000-0000-0000-000000000001`; verify scope before use |

The checkpoint contains 35 permanent workloads: 32 main-environment apps/jobs and three legacy jobs. The complete images and non-image invariant hashes are in the companion JSON. Do not confuse earlier counts of 65, 43 or 37 definitions with the current inventory; authorized diagnostic/manual retirements explain the changes.

At stage 44, all three vaults, both storage accounts and both registries still had public service access enabled. This is not anonymous access: authentication remains required. Backend network restriction is unfinished. Do not close the public website's Entra-authenticated ingress as a substitute for backend hardening.

Useful local runtimes:

```text
Project Python: ROOT\.work\venv-test\Scripts\python.exe
Azure CLI Python: C:\Program Files\Microsoft SDKs\Azure\CLI2\python.exe
Azure invocation: that Python executable followed by -m azure.cli
Python test import path: src;scripts;tests
```

The venv may print a benign stale CPython-location diagnostic. Verify the actual command result; do not interpret that diagnostic alone as test failure. The project venv does not necessarily contain all PDF tools. Use the environment's dependency discovery and applicable PDF/presentation skills when rendering. Azure Container Apps API `2026-07-01` exposed identity lifecycle restrictions omitted by older responses; preserve those restrictions.

## 5 Portfolio and document decisions that must survive handover

### 5.1 Calculation conventions

MS and T use HKD reporting and the inception-through-factsheet-date average of published one-month HIBOR fixings. Do not replace this with a reporting-month-only average. The owner confirmed HIBOR usage rights for this system and its factsheets. That confirmation is not a blanket licence for other benchmark or historical market data.

T uses January 2025 throughout its header/history convention, with validated fixed units beginning 20 January 2025. The owner rejected retaining November 2022 from the supplied template. Approved initial T exposures are 91% Greater China including Taiwan, 45% US Technology and Biotechnology, minus 49% Hang Seng, minus 45% S&P 500, plus 58% non-interest-bearing cash. These sum to 100% net assets with 230% initial gross risky exposure. Fixed units mean subsequent exposure weights drift; do not rebalance to those initial percentages silently.

The website now correctly describes MS/T model cash as 58 normalized NAV units, with current weight calculated against the corresponding normalized NAV. It is not literally HKD 58 and not a permanent 58% weight. Broker cash-history controls are suppressed only for derived/model cash, not physical accounts. Invalid inputs are withheld rather than replaced with zero.

MS and T are distinct models. Taiwan inclusion is important, but do not infer the exact MS constituent universe or clone T calculations into MS from a picture. Use approved `subject_calculation_definitions`, registered constituents, frozen inputs and their hashes.

For MS website comparisons the owner explicitly selected S&P 500 and HSI, while retaining HIBOR + Spread in the factsheets. Do not change all benchmark roles to HIBOR. Comparisons must use the appropriate reporting currency and common valid valuation dates. A Shanghai holiday-period last-open-session observation is not missing data to be fabricated or forward-filled without policy.

### 5.2 Factsheet and presentation design

The owner asked for reusable templates across all past and future monthly reports. Changes accumulated over several local and Azure releases:

- MS page 1 subtitle: “Multi-Tranche Quantitative strategy” below “Alpha World Enhanced Index”.
- Right-aligned “As of” and matching date font style at the top right of each page; T should match MS.
- Consistent one-line metadata headings, common value baseline and dividers for strategy inception, base currency, benchmark and risk profile.
- Consistent allocation box widths, caption spacing, branch-label placement and clean edges; T's allocation label is “Greater China Portfolio”.
- More intermediate X-axis labels in the annualized-return distribution while preserving the approved range and numerical data.
- T KPI layout matching MS except its actual monthly values; gray “Alpha World T Enhanced Index” caption and restored blue underline.
- Corrected footer spacing/typographical issues; removal of the private-template-preview stamp from the requested T layout.
- The T disclaimer wording is preserved. MS page 2 uses the owner's supplied T-style disclaimer including the Sprout Asset Management (Asia) Limited index-advisor paragraph, while retaining MS typography. T later adopts the latest MS disclaimer typography without changing T's words.
- Presentations use the editable 20-slide template, not raster-only slides.
- Friendly download names such as `AlphaWorld_MS_Factsheet_2026-09-30.pdf` and `AlphaWorld_MS_Presentation_2026-09-30.pptx`; equivalent T names. Internal immutable/content-addressed names remain intact.

There is a known narrative/model discrepancy. The owner repeatedly instructed that T's “About this Index” wording about dynamic rotation and leverage generally below 2x must not be changed. The approved calculation is fixed-unit with 230% initial gross risky exposure. Preserve the wording instruction, but keep this discrepancy visible; do not represent it as resolved, independently accurate, or authority to bypass a material release policy. The legal disclaimer was not to be rewritten as an incidental layout fix.

Installing a renderer does not rewrite old blobs or approve newly generated reports. Historical replacement requires a separately governed version, quality acceptance and appropriate approval. Preserve source files, approval history and previous released versions even when replacing the currently served download.

The requested website feature for future renderer/template updates is not fully established merely because migration 062 exists. Dormant schema and local tests for design updates are not proof that the complete self-service UI/workflow is deployed.

### 5.3 Source reconciliation and hypothetical risk

The missing 27–28 August 2026 broker cash records had previously been recovered from archived Flex evidence. Subsequent import logic must preserve valid target-only historical repairs. Archived exports can omit transaction type/IDs and contain summary/detail-looking pairs; the owner has no additional detail-only export. Reconcile exact keys and provenance, not row counts alone, and do not blindly deduplicate or invent transaction identifiers.

For hypothetical 2008–2009 stress tests, PSKY/SKYD could not legitimately be called direct verified historical security data without predecessor evidence. The owner approved the documented SPY proxy first for T, then for U16088571 and MS. This approval affects hypothetical stress only, not actual NAV, performance, composition or wording. The owner has no independent historical-data subscription. Matching stored data is not independent split/dividend-adjusted price certification.

The later SKYD/PSKY operational discrepancy was narrowed to contract ID `804144296`: dated broker positions used PSKY while the instrument master used SKYD. Two broker-position records and hashes supported the specific alias. Acceptance required matching identity, complete weights and classification. Do not broaden that alias into unverified historical corporate continuity or a universal ticker substitution.

## 6 Project history and work completed

### 6.1 Earlier foundation and onboarding

The project established tenant-scoped portfolio analytics, broker ingestion/reconciliation, filtered and composite portfolios, monthly reporting, governed review/release, owner administration and observability. Historical foundation documents are listed in section 12. They explain architecture and original decisions, but are not current deployment manifests.

MS was audited and moved from older USD conventions to approved HKD/HIBOR, with replay/cache safeguards, all-month presentation work, renderer/layout corrections, website HKD benchmark comparisons and calendar handling. T was built from its supplied PDF into a reusable template, linked to the approved January 2025 model, given a 20-slide presentation workflow, and taken through source/risk/report/onboarding qualification. Private previews were not equivalent to report approval.

Current catalogue evidence covers five subjects and 21 monthly periods each, January 2025 through September 2026. At stage 44, unrevoked approved artifact entries were 16 for U16088571, 16 for U16088606, 16 for Hong Kong/China, two for MS and zero for T: 50 entries total, not 50 approved months. T's reports are available for owner review, not internally released merely because its dashboard is active. Fresh viewer acceptance remains absent.

### 6.2 Original viewer report and October 9 diagnostic review

The owner clarified that the supplied `WeatlthRyse Data platform errors.docx` was the original 8 October report, not a newly recurring incident. Its suggested causes were treated as hypotheses, not executable instructions. Review findings are in `.work/platform-review-20261009-v1/review-report.html` with supporting sanitized metadata and reproductions.

Some original complaints were already corrected or required explanation: derived CSVs needed subject-aware queries; a derived portfolio has no independently executed trades; Shanghai was closed 1–7 October; aligned valuation count exceeds return-interval count by one; physical and derived subjects need different identifiers; redundant report date selection was removed. Earlier AI Analyst subject authorization was corrected in code/tests, but no fresh real-viewer-to-model acceptance was established.

The review identified twelve tracked areas:

| Finding | Original issue | Correction and remaining limit |
| --- | --- | --- |
| F01 | Authenticated frozen-snapshot export could select the pilot account without subject authorization | Scoped owner-only export and negative authorization coverage; retain cross-account tests |
| F02 | Monthly worker called `_existing_package` with unsupported replacement/snapshot arguments | Consistent exact-snapshot idempotency/replacement contract; corrected worker deployed; full scheduled cycle remains open |
| F03 | Account sync failed `449 != 447` cash-row reconciliation | Key/provenance-aware handling preserves valid recovered history; runtime qualified; actual freshness/cycle must be observed |
| F04 | MS assurance reported NAV ordering and stale layout mismatches | Currency/methodology/layout-aware assurance and benchmark/risk alignment work; later consumer fixes deployed |
| F05 | Burn-in reset after failed sync | Failure history preserved, labels made honest; actual 14-day acceptance still unverified |
| F06 | T lacked its first assurance result | Model core/crisis integration and assurance work progressed; no blanket financial certification inferred |
| F07 | Public backend networking and untested private administration | Private endpoints/admin/build and reversible trials added; final cutoff incomplete |
| F08 | Legacy HA and recovery exposure | Legacy zone-redundant HA plus private same-region recovery rehearsals; no cross-region guarantee |
| F09 | `/healthz` alone did not establish dependency readiness | Bounded dependency readiness and failure/recovery checks; liveness remains lightweight |
| F10 | Uneven diagnostics, image security and credential monitoring | Image-family qualification, audit/log work and metadata-only monitoring; rotation and human alert receipt remain open |
| F11 | Passing suites missed integration and authorization scenarios | Exact-runtime negative-access, worker, SQL/rollback, readiness and responsive tests added |
| F12 | NAV/derived-trade descriptions, aliases, negative zero and stale burn-in estimate | Scoped UI corrections; wider user/browser acceptance still needed |

The original review was risk-based, not a proof that every source line or ledger entry was independently audited. Historical findings about vulnerable packages do not constitute evidence of compromise.

### 6.3 Upgrade milestones

The following groups summarize the chronological journal; follow its links for individual build, execution, test, failure and protected rollback receipts.

| Milestones | Work completed |
| --- | --- |
| Early stages through 10 | Website/export/readiness corrections, key/provenance reconciliation, monthly-worker repair, risk/assurance work, additive private endpoints, same-region recovery and private administration/build qualification |
| 11 and 12 | Derived worker HKD benchmark refresh, assurance full-period versus comparison-window correction, sync and credential-broker runtime hardening, private service checks, legacy database HA and isolated failover rehearsal |
| 13 through 16 | Metadata monitor, institutional/discovery/sector, legacy ingestion and restricted audit/authority/observation runtime qualification; source preservation and actual identity boundaries checked |
| 17 | Exactly 22 authorized obsolete diagnostic jobs retired after dependency checks and protected archives; bootstrap runtime hardened |
| 18 and 19 | Scheduled administration/review and physical onboarding runtimes hardened; tests adjusted to each installed workflow rather than injecting newer features |
| 20 | Exactly seven more authorized diagnostic jobs retired: six dated synthetic trials and the inert log canary |
| 21 through 25 | Template intake, reconciliation, snapshot, registration and remaining legacy runtime families hardened; metadata-only credential monitor and owner-routed alert established |
| 26 through 30 | Accurate normalized MS/T model-cash display, missing private monitor endpoint, reversible legacy DNS/admin audit path, isolated PDF/PPTX workflow and physical BASE_SUMMARY display correction |
| 31 through 34 | Reversible main DNS rehearsal, credential grouping, five fresh QA inputs, material FX/sector/report-label discoveries, corrected reports and full historical input/NAV audit |
| 35 through 39 | Narrow SKYD/PSKY reconciliation, website/assurance currency consumers, onboarding renderer, monthly renderer and snapshot-worker deployments; two obsolete manual report jobs retired |
| 40 and 41 | Protected private admin/evidence path, 210-artifact history screening, native export of 105 decks, limited visual review, selected private-identity canaries and mandatory local cleanup |
| 42 | Report handover candidate, native tests and security qualification |
| 43 | Narrow duplicate MS September factsheet registration correction |
| 44 | Additive migration 062, current/fallback catalogue checks and live report-handover website deployment |

Runtime replacement was generally image-only: exact installed application files, commands, schedules, identities, secret references and non-image settings were preserved unless an explicitly scoped correction required otherwise. Older functional fallback images can still contain known high/critical vulnerabilities; “fallback tested” is not “fallback security-qualified.” Medium/low findings remain recorded on installed images.

Specific recovery evidence: legacy production PostgreSQL was upgraded to East Asia zone-redundant HA on General Purpose `Standard_D2ds_v5`, healthy primary zone 3/standby zone 1 at the recorded checkpoint, with 25 table fingerprints unchanged. An isolated clone planned-failover exercise recorded four failed connection samples and a maximum successful-sample gap of 30.056 seconds. This is not an unplanned-outage RTO guarantee. The clone was returned to its original compute/HA-off configuration; its zone placement did not return to the former zone. Main database HA predated this particular change.

Metadata-only credential monitoring lists three vaults without reading secret values. Eighteen enabled secret records lacked ownership/expiry/rotation tags in the captured evidence; absence of configured expiry is not proof of actual expiry. A later in-memory grouping resolved sixteen database-secret records into eleven connection/role groups, four password aliases and one retained historical-test credential. All sixteen inspected database login roles lacked enforced database expiration. No shared credentials were rotated.

The scheduled metadata monitor actually ran at 06:15 UTC, and its query alert fired. Provider-level test-message success and a fired alert do not prove a person received an email. Human receipt remains unverified.

Authorized retirement totals are 22 plus seven historical diagnostics plus two obsolete manual reporting jobs. Definitions, restore bodies and available executions were archived first. The final two were `ibkrv2stg-historical-reports` and `ibkrv2stg-presentation-v23-hist`; their 20 available execution records were preserved and the obsolete failure alert disabled with a restore body. Definitions can be restored; old Azure execution IDs cannot be recreated. No permission to delete unrelated databases, identities or report files should be inferred.

## 7 Material reporting defects and the private corrected batch

The upgrade found real reporting defects beyond cosmetic template changes. Native-currency holdings had been summed as if already in reporting currency; static benchmark captions could identify S&P 500 when the data represented HSI. Examples from frozen QA evidence: HKD 676,572 correctly converts to USD 86,222.33568; Greater China's mixed HKD/TWD holdings convert to USD 169,648.73435280. Its Technology allocation becomes 40.0353469%, rather than the erroneous 58.4802214% native-value aggregate. These are reporting/weight corrections, not a licence to alter broker NAV or actual returns.

The fix retains native values and dated account-scoped FX provenance, calculates reporting values and weights, rejects invalid/missing FX, removes misleading static captions and strengthens content validation. Website and AI/assurance aggregation consumers were separately reviewed; fixing only a PDF renderer was insufficient.

Historical QA includes 105 monthly inputs and 2,208 NAV intervals across all five subjects. Sixty-three reporting currency/sector corrections were prepared across the three non-composite subjects while preserving MS/T financial payloads. Twenty legacy U16088571 months lacked full monthly analytics because the old calculation intersected NAV and position dates, dropping the 21 January 2025 interval. The corrected full-NAV method uses a disclosed residual where holdings are missing rather than fabricating positions. Twenty legacy U16088571 inputs also needed per-security classification evidence reconstructed from their exact frozen values and sector totals; this is not independent provider confirmation.

U16088606 August 2026 had stale attribution covering 19 rather than 21 intervals. Its corrected attribution/provenance preserves NAV, monthly total return and cash. Corrections were versioned through successive QA stages; do not add the counts above into a supposed unique total or assume an early manifest contains the final August version. Trace the latest lineage and hashes explicitly.

The isolated original recovery source had 97 table fingerprints protected. Some rehearsals changed only the QA snapshot table and checked the other 96; initial report-isolation work cleared only five copied report-history tables, preserving the other 92 and the original recovery source. These counts are specific to those QA schemas. Production now has migration 062's additional tables, so do not reuse “97 tables” as a universal production invariant.

Transaction rollback and committed status rollback were rehearsed. **The production historical input migration is still pending.** Current renderers do not automatically replace old snapshots or registered files.

Private report verification achieved:

- 105 two-page factsheets and 105 twenty-slide presentations generated from the corrected QA lineage.
- Numerical, content, chart-data, bounds and non-finite-value screening across 210 PDF pages and 2,100 slides.
- All 105 decks opened read-only in native PowerPoint and exported without changing the source decks.
- Limited visual review: selected factsheet pages, earlier full September examples and, in the history pass, T September's twenty slides plus five attribution slides. This is **not** full visual inspection of every historical slide/page.
- Native worker parity checks for first/latest months across five subjects: exact PDF byte matches and canonicalized PPTX package matches, preserving charts and tables.
- Mandatory cleanup of 346 local review files at stage 41: 210 source QA files, 105 native PDF exports and 31 PNGs. Four later MS duplicate-comparison PNGs were also deleted. Azure originals and audit evidence were retained.

Some evidence JSON still records now-deleted local paths. That is expected after privacy cleanup, not permission to silently recreate or publish the files. The owner permits private downloads for review with deletion after use. Use the preserved Azure manifests and exact hashes if further review requires downloads.

## 8 The latest deployed reporting handover

### 8.1 Stage 43 duplicate correction already done

MS September 2026 had two active factsheet registrations. Registration 55 was the obsolete disclaimer version; 57 contains the owner-requested Sprout disclaimer. Both were associated with decision 28 and snapshot `7a8d6b37-f9cf-555d-9927-74b0389cae4e`. Page 1 was pixel-identical; page 2 differed as expected.

Registration 55 was narrowly revoked, retaining original blob and decision history. Its state revision became 2; registration 57 remained unrevoked at revision 1. Operation identifier: `ms-september-disclaimer-duplicate-20261011-v1`; audit event 90 records the exact before-state. Postcheck found zero duplicate active groups. **Do not repeat this mutation.**

### 8.2 Stage 44 live revision

```text
Revision: ibkrv2stg-web--report-handover-20261011-v1
Image: ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:211490e77d832fe92c2c1ab19e6fcab2ac6c52af8d88256b1b041678ac22214f
Build: ckd4
Candidate tag: report-handover-20261011-v3
Previous revision: ibkrv2stg-web--sector-fx-20261011-v1
Previous image digest: 7015b4dda6d6a2d91e24eff1bfd7d08c865681eb6165cd31c64645f9bf2861aa
```

The website was healthy with 100% traffic on this revision. Authentication and all unrelated workload bindings were preserved. Anonymous `/`, `/api/portfolio/U16088571/holdings` and `/api/portfolio/alpha-world-t/cash` checks returned 401. Stage 44 did not establish a new owner or viewer browser acceptance result.

The change allows an existing approved, unrevoked download to survive calculation-snapshot replacement while a new report remains private and marked replacement-pending. It preserves tenant, subject, period, artifact type, decision, snapshot, generation, quality and integrity checks. Multiple active releases are rejected. A new authorized approval transactionally supersedes an older release for the same month/type while preserving audit history. It does not auto-approve the corrected batch.

Only three source modules plus image release metadata were in the candidate delta:

| Module | Qualified SHA256 |
| --- | --- |
| `src/ibkr_etl/monthly_release.py` | `ce66889ad3ae4de4700ee9adb533baa7a39ef63c1e75ac6efd7288490096f0d8` |
| `src/ibkr_etl/monthly_reports.py` | `f046582a66a455f6c1fd1bf146c3130e7af69003caab4f0458f1cc3417a383f7` |
| `src/ibkr_etl/web.py` | `be147218f564becafb76a4ee4970410cddc330cf823bbc84af9f1ab82a567cf3` |

These are qualified candidate contents, not a claim that the current broader workspace files are identical. Native image tests: 72 passed. Deployment/role guards: 14 passed. Local candidate regression included 94 tests plus 105 subtests; 22 broader local design tests do not prove deployment of that design feature. The candidate scan found zero critical/high/unknown findings; 35 medium and 18 low findings remain.

### 8.3 Production migration 062

`migrations/postgres/062_report_design_updates.sql` is now applied. Its qualified hash is `63710f9e0d6d0e305dc8ec4fefd1389b781f864b35370c3005409fbdf5e00ae9`.

It adds `report_artifact_releases.superseded_by_artifact_release_id`, same-tenant identity/foreign-key and supersession constraints, and three forced-tenant-RLS tables: `qualified_report_designs`, `report_design_update_requests`, `active_report_designs`. The migration ledger records application. The design tables are dormant for this release. No report approval, broker data or database role permission was changed by stage 44.

The previous image remains compatible with this additive schema. Current and fallback native catalogue checks each covered five subjects times 21 months and preserved all 50 approved artifact entries. Application rollback should not drop the additive schema. A transaction rollback rehearsal passed; there was no forced live website rollback interruption.

### 8.4 Protected checkpoint identity

```text
Local: .work/platform-upgrade-20261011-stage44/closeout.json
Protected SHA256: 13f13a32117b3c44a7711ca957682934c30c36ea97e6259c9a907e2e46b39ac2
Protected blob: acceptance/gateway-read-upgrade-readers-platform-upgrade-20261011-stage44-closeout-13f13a32117b3c44a7711ca957682934c30c36ea97e6259c9a907e2e46b39ac2.json
Version: 2026-10-11T20:04:10.4237706Z
```

Verify protected bytes using the existing receipt helper. The local JSON contains an embedded receipt, so do not assume hashing the entire local wrapper must equal the protected payload hash.

## 9 Owner authorization and safety boundaries

The following are recorded owner decisions, not a grant of authority from this document itself. The successor should operate under the owner's current instructions and applicable tool policy, and stop on material scope changes.

| Owner decision | Scope and boundary |
| --- | --- |
| Work on Azure/computer and finish the upgrade | Scoped project corrections, not unrelated resources or unrestricted disclosure |
| Maintenance window “now” | Historical approval; reconfirm practical timing if resuming later or affecting current users |
| Best solution, price not an issue | Quality prioritized; still avoid unnecessary resources and explain material changes |
| Private administration/build VM and Bastion approved | Private path before cutting existing public paths; no public VM IP |
| Recovery data stays in East Asia | No cross-region portfolio copy authorized; exact RPO/RTO numbers were not specified |
| No external credential consumers | Owner stated no outside spreadsheets/scripts/services; verify any newly discovered dependency |
| No viewer available | Leave real viewer sign-in explicitly unverified; do not impersonate a viewer or treat owner as one |
| Fourteen-day observation | Keep unverified until actually completed; do not reset, hide or relabel failures |
| Source copy into private Azure audit | Application source only, not credentials or broker records |
| August private build snapshot | One existing approximately 180 KB test snapshot approved; not blanket permission for full database-fixture uploads |
| QA container access | Owner read-only access limited to QA output container; not production report or viewer grants |
| Private historical batch download | 105 PDFs and 105 decks may be downloaded for checking, then local copies/exports deleted |
| Native PowerPoint automation | Read-only opening and separate exports; preserve Azure originals and delete local review material after use |
| Job retirements | Specific 22, seven and two job sets only, after dependency checks and evidence preservation |
| Report wording and HIBOR/proxy decisions | Preserve the exact scope described in section 5 |

Grok previously made overlapping changes; the owner later said Grok had stopped. The owner is now handing the project to another AI. Establish one deployment writer before further live work. No successor was contacted and no new agent was launched during preparation of this document.

Do not include secret values, password hashes, broker tokens, full connection URLs or SAS URLs in logs, build context, this handover or chat. Keep raw broker records and sensitive portfolio artifacts private. Preserve source records, historical evidence, report originals and approval history.

Internal/shareholder access is distinct from client distribution. Provisional benchmark licensing/disclosure controls must remain enforced. HIBOR rights and an owner liking a preview do not authorize every new corrected historical release or client publication.

## 10 Remaining implementation and acceptance plan

### Step 1 Establish a fresh baseline and sole change ownership

Read stage 44 and restore/verify its protected receipts. Inventory current 35 workload definitions, image digests, non-image invariants, web revision/traffic/authentication, migration ledger, release counts/duplicates, service network settings, DNS links and active jobs using read-only methods. Compare with the companion JSON. Any drift requires explanation before mutation; do not weaken the guard to force a pass.

Confirm private administration and audit evidence remain usable. Preserve a fresh protected baseline and choose a new stage/folder for new work. Do not overwrite old source, attempt folders or receipts. Verify there is no concurrent deployer.

Exit evidence: signed-off scope, exact installed source/image lineage, recoverable baseline and no unexplained drift.

### Step 2 Finish governed production reporting-input correction

Trace the final 105 QA inputs through currency/sector correction, full-NAV enrichment, August attribution repair and the newest NAV audit. Verify against current production source hashes and dates. Do not use an early QA inventory that omits later corrections.

Prepare a versioned migration plan specifying each old/new snapshot identity, subject, month, financial differences, preserved fields and evidence. Use exact tenant/role boundaries, concurrency/idempotency checks and committed rollback. Rehearse on an isolated copy with current schema, including migration 062. Only then apply a scoped production change and independently verify all intended records plus unrelated invariants. Do not modify raw IBKR history or invent missing positions/transactions.

Exit evidence: current-source reconciliation, approved scoped plan, successful isolated rehearsal and rollback, production readback and full lineage. Stage 44's schema migration is not this financial-input migration.

### Step 3 Complete report acceptance and replace reports through governance

Finish visual acceptance for the historical PDFs/decks, keeping a per-file/page/slide matrix. Automated text/bounds/chart checks and native export do not count as visual inspection. Review clipping, font substitutions, chart labels/units, benchmark identity, ticks, table overflow, dates, disclaimers and actual numerical reconciliation.

Use current qualified rendering workers to generate/register the production correction batch privately. Prove fresh generation, unchanged retry, second-upload failure rollback and recovery, not only reuse of existing blobs. Maintain exact hash/size and snapshot-generation linkage. Obtain appropriate new owner approval before release; do not transfer approval from an obsolete file by copying a flag.

Exercise the deployed handover: old approved download stays available while replacement is pending; new approval atomically supersedes the old release; revoked/wrong-subject/wrong-tenant/wrong-month/integrity-failed artifacts remain inaccessible. Verify actual website downloads and friendly names against accepted files. Keep T at zero released artifacts until its reports are legitimately approved.

Delete only exact reviewed local copies/exports after use and record cleanup. Preserve Azure originals and receipts.

### Step 4 Complete private-network qualification and carefully cut over

The earlier main-network plan groups callers into 15 groups numbered 3 through 17. Fresh stage 41 canaries covered only groups 9, 12 and 17. Their three jobs were retired and the three temporary main DNS links rolled back. Nine private DNS/TLS peers were checked per canary; these were not normal broker/DB-writing application executions.

Build a successor plan against the new stage 44 boundary. Recreate only needed reversible links/canaries under fresh names. Verify every legitimate caller and recovery path, including platform-managed Key Vault secret resolution/refresh, uncached private ACR image pull, private build/push, administration, storage and database TLS. A process-local request pinned to a private IP does not prove the platform's managed secret resolver or image-pull path works with public access disabled.

Some identities intentionally have `identitySettings.lifecycle=None` and cannot acquire runtime tokens. Do not grant token rights to make a diagnostic pass; qualify their platform-level paths correctly. Broker set-only authority must not be broadened to get-secret authority.

After all consumers pass, stage backend public-access restriction with a tested rollback and negative-external tests. Preserve Entra-protected website availability. Do not switch off all seven services at once based only on endpoint creation or DNS success.

### Step 5 Rotate credentials with dependency and recovery proof

Refresh the metadata-only consumer inventory, confirm actual role/secret coupling in memory without logging values, test recovery/reconnection and rotate one family at a time. Use least privilege and maintain a secure break-glass/admin path. Verify each consumer resolves the new version and resumes normal operation before invalidating an old credential. Preserve audit metadata and distinguish query IDs/configuration from secrets.

No rotation has yet been completed. The owner's statement that there are no external consumers helps scope discovery but does not prove every in-resource-group dependency or cached credential has been exercised.

### Step 6 Complete operational acceptance

Run broad authenticated owner website checks across all subjects and date selections, exports, charts, report views, error handling and responsive widths. Retain negative authorization, readiness outage/recovery and download integrity checks. Schedule a real viewer acceptance when a real viewer becomes available; until then state UNVERIFIED.

Observe a genuine dependency-ordered scheduled cycle using fresh eligible broker source, not a manually replayed stale export. A prior source file was correctly rejected at 16.259 hours against a 12-hour freshness limit; do not loosen that limit to pass a demonstration. Track actual source ingestion, derived/benchmark/risk updates, assurance and reporting.

Allow the actual 14-day success window to accrue under its valid rules. Preserve genuine failures. Confirm alert receipt with the owner; provider completion and Azure alert firing are intermediate evidence only.

### Step 7 Close remaining policy and maintenance gaps honestly

Independent adjusted crisis-price certification remains unavailable. Disclose the documented proxy and evidence limitations; do not claim independently verified direct 2008 histories. Keep the retained T narrative discrepancy visible without changing the owner's wording.

Register validated 2027 exchange calendars before processing dates outside the current coverage. Track medium/low vulnerability findings and rescan exact installed images as databases evolve. Confirm diagnostic retention/expiry and review retained trial-database disposition separately: stopped Azure PostgreSQL can restart automatically after seven days, so “Stopped” is not an indefinite retirement plan. Do not restart the previously canceled six-specialist/agentic rollout without new authorization.

Clarify quantitative RPO/RTO with the owner if required to declare recovery acceptance. Same-region HA and clone tests do not prove regional disaster recovery. The requested self-service template-update feature needs separate end-to-end qualification if brought into scope; dormant migration tables are not completion.

Finish with one protected closure matrix distinguishing implemented, verified, not tested and owner-accepted exceptions. Never label an unperformed check passed. Full upgrade completion must not be inferred merely because remaining work needs time or an external reviewer.

## 11 Safe continuation and known traps

1. Preserve the dirty workspace and existing user changes. Do not reset or replace the entire checkout. No repository-wide rebuild is qualified by this handover.
2. Inspect any controller before invocation. Many functions named `plan`, `probe`, `read` or `rehearse` still upload protected evidence, start Azure jobs, patch a temporary execution or use privileged identities. A filename is not proof of read-only behavior.
3. Protected attempts are immutable. `control_upgrade_readers_20261010.save` refuses overwrite. Use a new attempt/folder rather than editing evidence or changing dispatched payloads.
4. An Azure request can succeed despite local acknowledgement failure. Stage 44's PATCH succeeded, but a missing `properties` field caused a local KeyError. It was reconciled against the actual ready revision, image and traffic; there was only one PATCH. Use `apply-reconciled.json`, not a blind retry.
5. Logs can arrive late. Reconcile execution IDs and intended hashes before resubmission. Do not start duplicate workflows because one result line is absent.
6. Do not grant INSERT/UPDATE to the website role. The initial handover preflight incorrectly assumed it needed writes and stopped before mutation. Its successor checks the separate approval role. Preserve the recorded failed attempt.
7. Preserve source and dependency compatibility per installed image. Tests for newer modules failed against older jobs because those features were absent, not because the live job necessarily needed the newer application. Correct test scope without hiding genuine failures.
8. `release_report_handover_20261011.unchanged()` binds an older stage 42/website boundary. It is expected to reject the now-deployed stage 44 state. Create a reviewed successor boundary, not a bypass.
9. `prepare_reporting_migration_clone_20261011.py` routes an existing administrative credential to the QA host. It is not a production migration shortcut. Respect its destination guard.
10. Qualified rollback is multidimensional: image fallback, database transaction/status rollback, DNS restoration and artifact/release history are separate. Do not equate one with all others. Do not drop migration 062 during web-image rollback.
11. Old fallback images may have serious known vulnerabilities. Use only a necessary controlled recovery with a plan to return to a qualified image, not permanent silent downgrade.
12. Local cleanup must validate absolute targets inside the review directory and operate on exact files. Never recursively delete the workspace, output root, Azure originals or audit receipts. Original execution IDs cannot be restored even when job definitions can.

These initial commands read local evidence only; they do not authorize a deployment:

```powershell
Set-Location -LiteralPath 'C:\Users\antho\Downloads\IBKR Data\IBKR Portfolio Platform v2'
Get-Content -Raw -LiteralPath '.work/platform-upgrade-20261011-stage44/closeout.json'
Get-Content -Raw -LiteralPath 'docs/AI_AGENT_HANDOVER_2026-10-11_inventory.json'
Get-Content -Raw -LiteralPath 'scripts/deploy_report_handover_20261011.py'
Get-Content -Raw -LiteralPath 'scripts/handover_role_boundary_20261011.py'
```

Do not paste a generic bootstrap/migrate command from an old README into production. Inspect current migrations, exact target, role, source hashes, active executions and rollback evidence first. Use the existing authenticated Azure context; do not ask the owner to paste credentials into chat.

## 12 Evidence and code navigation

### 12.1 Primary evidence

| Relative path | Purpose |
| --- | --- |
| `.work/platform-upgrade-20261011-stage44/closeout.json` | Latest sealed state and protected receipt graph |
| `.work/platform-upgrade-20261009-v1/deployment-progress.html` | Detailed chronological work, failures, deployments and links |
| `.work/platform-upgrade-20261009-v1/upgrade-plan.html` | Original scope and staged correction plan |
| `.work/platform-upgrade-20261009-v1/release-gates.json` | Original gate definitions; historical statuses, not current truth |
| `.work/platform-review-20261009-v1/review-report.html` | Original F01–F12 diagnostic/code review |
| `.work/viewer-report-investigation-20261008/` | Earlier viewer investigation and candidate/maintenance evidence |
| `.work/alpha-t-onboarding-completion-20261009/` | Broker reconciliation, HIBOR, identity/proxy and supplemental evidence |
| `.work/alpha-world-t-accuracy-audit-20261009/` | Earlier T numerical audit |
| `.work/platform-upgrade-20261011-stage34/` | Historical reconciliation, corrected snapshots, reporting image and audits |
| `.work/platform-upgrade-20261011-stage40/` | Historical private download/manifests and screening |
| `.work/platform-upgrade-20261011-stage41/native-review-evidence.json` | Native exports and explicit visual-review limits |
| `.work/platform-upgrade-20261011-stage41/local-cleanup-completed.json` | Exact privacy cleanup evidence |
| `.work/platform-upgrade-20261011-stage42/handover-image-v3/` | Qualified reporting-handover build |
| `.work/platform-upgrade-20261011-stage43/closeout.json` | Completed MS duplicate-registration correction |

Stage 34 subfolders to inspect include `historical-reconciled-read-v1`, `snapshot-migration-v2`, `legacy-monthly-plan-v3`, `monthly-snapshot-enrichment-v1`, `august-snapshot-correction-v1`, `all-monthly-nav-audit-v3`, `factsheet-history-audit-v1` and the subject-specific `historical-outputs-*` directories. Later versions supersede earlier attempts only where explicit protected lineage says so; preserve failed/earlier evidence.

Within `.work/platform-upgrade-20261011-stage44/`, schema evidence is under `migration-v2/schema-preflight-v1`, `migration-v2/schema-rehearse-v1` and `migration-v2/schema-apply-v1`. Catalogue evidence is in the sibling folders `catalogue-fallback-v1` and `catalogue-candidate-v1`, not inside `migration-v2`. Website evidence is in `web-deployment-v1`. Consult the closeout's receipt graph if a local file is absent. The web folder contains the planned change, verified state and acknowledgement reconciliation.

### 12.2 Controllers and tests

These are navigation references, not a list to execute sequentially. Read their guards, expected stage, target host and mutation behavior first.

| Workstream | Files under `scripts/` |
| --- | --- |
| Reporting input correction | `prepare_reporting_snapshot_correction_20261011.py`, `legacy_reporting_reconciliation_20261011.py`, `rehearse_reporting_snapshot_migration_20261011.py`, `control_reporting_snapshot_migration_v2_20261011.py` |
| Monthly coverage/enrichment | `prepare_monthly_snapshot_enrichment_20261011.py`, `control_monthly_snapshot_enrichment_20261011.py`, `control_legacy_monthly_enrichment_v3_20261011.py`, `read_legacy_monthly_enrichment_v3_20261011.py` |
| Reporting image/readiness | `release_reporting_full_nav_20261011.py`, `control_reporting_history_readiness_v2_20261011.py`, `read_reporting_history_readiness_v2_20261011.py` |
| Isolated clone | `prepare_reporting_migration_clone_20261011.py`, `control_reporting_migration_clone_20261011.py` |
| Handover schema and roles | `migrate_report_handover_20261011.py`, `control_handover_migration_v2_20261011.py`, `probe_handover_permissions_20261011.py`, `handover_role_boundary_20261011.py` |
| Catalogue and website | `probe_handover_catalogue_20261011.py`, `control_handover_catalogue_20261011.py`, `deploy_report_handover_20261011.py`, `reconcile_handover_deployment_ack_20261011.py`, `seal_upgrade_stage44_20261011.py` |
| Private canaries | `prepare_upgrade_private_canaries_20261011.py`, `control_upgrade_private_canaries_20261011.py`, plus its v2/v3 successors |
| Focused safeguards | `test_handover_role_boundary_20261011.py`, `test_handover_deployment_20261011.py`, `test_reporting_snapshot_correction_20261011.py`, `test_monthly_snapshot_enrichment_20261011.py`, `test_upgrade_private_canaries_20261011.py` |
| Protected evidence helpers | `control_upgrade_readers_20261010.py`, `control_upgrade_legacy_recovery_probe_20261009.py`, `rehearse_ms_worker_image_rollback.py` |

Use `rg --files` and `rg -n` for precise discovery. Build and execution manifests identify exact source and test hashes. Archive helpers restore/hash-verify protected evidence; they do not turn an outdated controller into a valid current plan.

### 12.3 Longer term project documentation

Read as architectural/history context, then reconcile with the current checkpoint:

- `README.md`
- `docs/SYSTEM_AUDIT_2026-10-01.md`
- `docs/SYSTEM_REVIEW_2026-10-06.md`
- `docs/SYSTEM_IMPROVEMENTS_2026-10-06.md`
- `docs/APPROVED_UPGRADES_EXECUTION_2026-10-01.md`
- `docs/AZURE_NATIVE_RELEASE_FOUNDATION_2026-10-01.md`
- `docs/IMPLEMENTATION_TRACKER.md`
- `docs/INSTITUTIONAL_ONBOARDING_ARCHITECTURE.md`
- `docs/OBSERVABILITY_WATCHDOGS_2026-10-01.md`
- `docs/OPERATIONAL_DATA_MONITORING_2026-10-01.md`
- `docs/OWNER_USER_MANUAL.md`
- `docs/PILOT_U16088571_DATA_CONTRACT.md`
- `docs/STAGING_ARCHITECTURE_AND_COST_PLAN.md`
- `docs/ALPHA_WORLD_MS_COPY_CORRECTIONS_2026-10-07.md`
- `docs/ALPHA_WORLD_MS_DISTRIBUTION_TICKS_2026-10-07.md`
- `docs/ALPHA_WORLD_MS_HKD_CUTOVER_2026-10-07.md`
- `docs/ALPHA_WORLD_MS_PRESENTATION_QUALIFICATION_2026-10-07.md`
- `docs/ALPHA_WORLD_MS_WEBSITE_ACCEPTANCE_2026-10-07.md`

## 13 Suggested opening instruction for the successor agent

> Read `docs/AI_AGENT_HANDOVER_2026-10-11.md`, its inventory JSON, stage 44's protected checkpoint and the chronological progress journal. Confirm sole change ownership, then perform a read-only live drift check before proposing or applying new mutations. Continue the remaining governed production reporting corrections, private-network and credential work using exact-source qualification and tested rollback. Preserve broker data, original files, tenant authorization and approval history. Do not rerun completed migration 062 or the stage 43 duplicate correction. Keep the 14-day observation, real viewer sign-in, whole scheduled cycle, human alert receipt, independent crisis-price certification and full historical visual review explicitly unverified until supported by actual evidence. Obtain any new authority needed for materially changed scope. Do not mark the full upgrade complete merely because runtime deployments are complete.

This handover was prepared without deploying, rotating credentials, changing access, publishing reports, contacting another agent or modifying historical evidence. Its companion inventory is a selected metadata extract, not a live Azure export or a secret backup.
