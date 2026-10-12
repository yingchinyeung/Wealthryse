# IBKR Portfolio Platform v2

## Current release channel

The Azure portal is a private, multi-account internal-production system for
authorized company and shareholder use. Provisional benchmark and sector data
come from yfinance. Internally accepted reports are stored in the private
`internal-reports` container. Client distribution is database-blocked until a
licensed provider such as Bloomberg is installed and the final disclosure is
approved.

This workspace contains the private Azure internal-production portfolio website,
deterministic analytics engine, monthly factsheet workflow, presentation-deck
workflow, and controlled AI administrator/portfolio analyst. Staging and
release controls remain logically isolated from the existing IBKR ingestion
baseline.

## Safety boundary

- The existing IBKR ingestion baseline remains in `rg-ibkr-database-v11`; the
  website and report-release resources are logically isolated within that
  resource group.
- The current production ETL baseline is release `20260922-hkc-cash-ledger-r14-1`.
- The first website pilot account is `U16088571`.
- Staging must pass automated tests, financial reconciliation, access-control tests, and user acceptance before any production cutover.
- Secrets, raw Flex XML, databases, generated reports, and archives are excluded from source control.

## Operational baseline — 1 October 2026

The active account directory includes U16088571, U16088606 and China & Hong
Kong. Access is Entra-authenticated and account-entitlement controlled for the
owner and four registered colleagues. Alpha World MS onboarding remains paused.
The original IBKR refresh runs at 16:00 HKT (08:00 UTC); downstream portfolio,
benchmark, risk and report jobs retain their existing Azure schedules.

Build the web and reporting workloads with **Dockerfile**, never the
ingestion-only `Dockerfile.azure`. The full image validates its approved PDF and
PPTX templates, Node/Artifact Tool import and native chart canvas at build time.
Both builds install hash-locked `requirements.lock` dependencies exported from
`uv.lock`. Regenerate that file after an intentional lock update:

```powershell
uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file requirements.lock
```

Use immutable image digests, retain previous images, and record the verified
rollback target after live checks. `scripts/release_verified_image.py` records
before/after image pins and verifies that unrelated workload settings stay
unchanged. It is a controlled local release tool, not an enabled CI/CD service.

The system-health page aggregates every active physical/derived portfolio's
latest assurance; missing or stale evidence is not a pass. During hybrid
authorization, a completed database denial is authoritative. Only the exact
configured owner retains emergency legacy access during a database outage.

See [the detailed audit](docs/SYSTEM_AUDIT_2026-10-01.md) for evidence, changes,
remaining limitations and the prioritized upgrade plan. No audit is a guarantee
that every future failure has been eliminated.

## Historical implementation notes — superseded by the operational baseline

The following records describe earlier rollout milestones. Their revision
numbers, account counts, acceptance counts and pilot-only restrictions are not
the current production configuration; consult the live portal and audit above.

Phase 7 — the U16088571 internal-production database and Entra-protected private
portal are deployed. HTTPS-only external ingress and the approved-user browser acceptance
test were completed on 22 September 2026. Only the explicitly allow-listed pilot
user can access portfolio routes. An internal-use yfinance job uses the S&P 500
Price Index (`^GSPC`) as U16088571's primary internal benchmark and the
NASDAQ Composite Price Index (`^IXIC`) as its selectable secondary benchmark.
Both feeds remain provisional and client publication remains disabled.
The portal defaults to the latest validated portfolio date and includes an
`As of` selector that consistently reloads NAV, history, holdings and benchmark
comparison data through the selected date.
The same account-scoped view also provides trailing cash activity, cash
balances, deterministic risk metrics, CSV downloads and system health. A
read-only trade-history view exposes authoritative IBKR executions with date,
symbol and buy/sell filters; it displays quantity, execution price, proceeds,
commission, net cash and realized P&L and exports the complete filtered result
to CSV. Trades arrive through the existing daily account synchronization.
It also includes an authenticated monthly report centre. The selector shows all
20 frozen month ends from January 2025 through August 2026 and distinguishes a
released package from a month whose factsheet and presentation are still queued.
The accepted August 2026 factsheet PDF and 20-slide v23 editable PowerPoint can be
downloaded directly from the website after an integrity check. Downloads are
streamed by the authenticated API from the private `internal-reports` container;
the browser never receives a storage key or SAS URL.
Risk Analytics includes historical 95% Daily Expected Tail Loss, top and bottom
five position contributors with an explicit residual, and deterministic
one-year expected, actual and tail maximum-drawdown measures. The fixed
15 September 2008–9 March 2009 crisis drawdown is a hypothetical fixed-unit
replay. The current release resolves holdings independently for each chosen
as-of date, never uses a future holdings snapshot, and uses direct adjusted-close
history when complete, recorded sector or broad-market proxies otherwise, and
flat cash.
An independent job backfills and refreshes the official U.S. Treasury
3-month par yield at 07:00 HKT daily. The portal uses the latest validated
observation on or before the selected as-of date to display the risk-free rate
and calculate Sharpe.
The portal now enforces an eight-hour fixed Entra session, five-minute nonce
validation, HTTPS-only access and a two-factor application allow-list (email plus
Entra object ID). Every application request emits a correlation ID and a
privacy-preserving structured audit event; raw identity claims, query strings,
cookies and tokens are never written to the event. Browser responses are marked
non-cacheable and include content-sniffing, referrer and frame-embedding
protections. The website database credential remains read-only.
Phase 5 has begun with seven deterministic, account-scoped analyst tools for
portfolio summary, NAV history, holdings, benchmark comparison, risk metrics,
data freshness and sector allocation. The tools use a fixed registry and static
read-only queries, validate the account before database access, attach structured
source citations and write hashed request/response audit evidence. U16088571
ticker symbols are classified by an internal-use Yahoo Finance
search-profile job. Only ticker symbols leave Azure; the database stores no raw
Yahoo response, marks the source and licence as provisional/restricted, and
keeps client publication disabled.
The grounded narrative code and privacy-minimizing model payload are now
implemented and covered by fail-closed tests. In addition to the fixed briefs,
the portal provides an interactive question mode backed by the same seven
read-only tools. Exact figures and chart series are produced by deterministic
code; the model supplies qualitative interpretation and cannot change the
validated as-of date, invent citations or introduce numerical claims. A daily
17:10 HKT portfolio-analyst assurance job independently reconciles NAV,
performance, holdings, risk, benchmarks, sectors, freshness and all future
factsheet/presentation template versions before the 17:15 HKT acceptance check.
The keyless Foundry resource is
in Southeast Asia and the explicitly approved `gpt-5.6-sol` deployment uses the
APAC Data Zone pay-as-you-go boundary. The model receives only sanitized,
structured U16088571 results and has no tools or database access. A live
grounded portfolio-overview test completed successfully on 22 September 2026
with strict schema validation, supplied-source citations and token-only audit
evidence.

The portal also has a controlled administrator workflow for two exact
market-data maintenance operations: refresh the Treasury risk-free series and refresh the
provisional benchmark series. A user must create and review a ten-minute
preview, then type the exact approval phrase. The approval is bound to the same
Entra actor and payload digest and is consumed atomically once. The web identity
can start only those two named staging jobs; it cannot run production, IBKR/Flex
or arbitrary jobs. The AI model has no operational-action tools and cannot
approve or execute an action. Portal revision `ibkrv2stg-web--0000043` is the
active validated release. It provides an account directory and separate,
account-scoped portfolio subpages, retains the company logo in the responsive
header under the `WealthRyse Portfolio Data Platform` name, and preserves tenant- and account-scoped
download for the latest frozen reporting snapshot. The corresponding snapshot
job also publishes the integrity-verified JSON to the private `report-drafts`
container using its dedicated managed identity; no public blob access was
enabled.

The system also runs the `controlled-autonomy-v1` administrator supervisor in
Azure every five minutes. It records health runs and incidents, asks the
approved regional GPT-5.6 Sol deployment to explain only newly opened structured
incidents, and permits deterministic policy code to start at most one
allow-listed benchmark or Treasury-rate refresh per run with a six-hour
cooldown. It cannot retry IBKR, access secrets, trade, execute arbitrary SQL or
shell commands, change formulas or permissions, deploy code, delete financial
history, or expand its own authority. Those operations remain prohibited or
owner-approval controlled. The Administration page displays the live status,
open incidents, cooldown and recent autonomous-action evidence.

Stages 3 and 5 of the controlled-autonomy rollout are active. After three
consecutive portal authentication-boundary failures, the deterministic broker
may activate only the registered last-known-good portal revision
`ibkrv2stg-web--0000041` (image digest
`sha256:683d76d3e9817fb84254b14d48abd83e931aba100aaa14913295a2419b26583d`).
Its custom Azure role can read revisions and invoke exact revision activation;
it has no general Container App write, deployment, deletion, secret or role
authority. The `ibkrv2stg-autonomy-review` job creates a fixed-schema monthly
authority review at 09:00 HKT on the first day of each month. Owner approval is
required by the displayed due date; an overdue review pauses repairs while
health monitoring continues. The current owner authority expires on
15 October 2026 unless renewed. Stage 4's formal, non-retroactive single-user
acceptance window started on 24 September 2026. The zero-retry burn-in monitor
runs daily at 17:15 HKT and records at most one result per HKT date in the
tenant-isolated acceptance ledger. Day 1 passed and the current consecutive count
is 1 of 14; the earliest possible completion date is 7 October 2026. A failed or
missing day breaks the consecutive count, and earlier operational telemetry is
not credited.

The current reporting snapshot is frozen for the completed month ending
31 August 2026. Snapshot `751c2b5a-c9cd-54fb-808e-ff98edbd8d67` has data as of
31 August 2026. Its integrity hash is stored with the immutable database and
blob records; a repeated job run returned the same ID and hash without inserting
a duplicate. It is an internal reporting snapshot
with `warn` quality and publication disabled because the two Yahoo benchmark
feeds and the complete Yahoo sector classification remain restricted. The
manual staging job has retry limit zero and is pinned to immutable image digest
`sha256:43e4f93e80e5521de275bd2877fb103e4157c46719a26958edb9ddd8a6709987`.
The frozen snapshot has been used to produce and visually reconcile a two-page
factsheet and a 20-slide v23 editable presentation. The expanded presentation adds
a deterministic executive summary, exposure-and-concentration analysis,
monthly security contribution, sector return drivers, dividends and financing,
and a month-end conclusions/watchlist page without copying reference-client data. The presentation retains
the approved Sprout issuer, `BDW665`, Types 1, 4, 6 and 9, and the confirmed
contact email. Publication remains blocked at the system level until licensed benchmark and
sector sources are connected and the complete regulatory disclosure is approved.

The source also includes a fail-closed historical month-end batch for
U16088571. It validates continuous monthly source coverage, classifies any
previously unseen historical holdings, creates a date-specific crisis replay
and immutable snapshot, uploads the verified
snapshot, and idempotently queues both registered report templates. The
January 2025 through August 2026 range contains 418 valid date-specific
holdings snapshots and 20 reporting months.
The Azure historical job is manual, single-replica and zero-retry. The approved
historical batch completed 20 month-end snapshots for January 2025 through
August 2026, after which its Yahoo transmission gate was returned to disabled.

The live internal portal updates automatically from the existing production
export. Production IBKR ingestion remains at 16:00 HKT. At 16:10 HKT a
least-privilege job verifies the export manifest and hash, filters it to
U16088571 only and imports the resulting snapshot idempotently. At 16:30 HKT
the S&P 500, NASDAQ Composite and date-specific crisis replay are refreshed.
The Treasury risk-free series remains scheduled for 07:00 HKT. The 23 September
live run advanced the portal through 22 September 2026 and the authenticated
browser check showed system-health status `pass`.

Staging portal:
<https://ibkrv2stg-web.purplerock-8cff24a2.eastasia.azurecontainerapps.io/>

See:

- `docs/BASELINE_AUDIT_2026-09-22.md`
- `docs/PILOT_U16088571_DATA_CONTRACT.md`
- `docs/IMPLEMENTATION_TRACKER.md`
- `docs/STAGING_DATA_AND_PORTAL_RECORD_2026-09-22.md`

## Local verification

From this directory in PowerShell:

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m unittest discover -s tests -v
```

Expected result for the current source baseline: all 166 automated tests
passing.
