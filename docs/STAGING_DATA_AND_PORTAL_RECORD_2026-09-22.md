# Staging Data and Portal Record — 22 September 2026

## Completed scope

The second staging stage added versioned database migrations, loaded a reviewed
U16088571-only snapshot, reconciled the imported data, and deployed a read-only
single-user portal, enabled its approved HTTPS-only ingress, and completed live
browser acceptance as the allow-listed user. Production was not changed.

## Versioned database foundation

The staging bootstrap applied and recorded:

1. `001_init.sql`
2. `002_platform_reporting.sql`

The second migration adds tenant-isolated tables for:

- benchmark definitions and official observations;
- reference FX and risk-free rates;
- portfolio/benchmark mappings and daily comparisons;
- metric definitions and data-quality results;
- immutable reporting snapshots and report-generation runs;
- application users and account access;
- agent tool audit, approved actions and general audit events.

All 14 tenant-specific platform tables have PostgreSQL row-level security enabled
and forced. `metric_definitions` is intentionally global versioned metadata.
Migration filenames and SHA-256 checksums are recorded in `schema_migrations`;
changing an already applied migration now fails closed.

## Reviewed U16088571 snapshot

Source: the locally downloaded analyst snapshot generated at
`2026-09-22T08:34:51+08:00`. The source passed SQLite integrity and foreign-key
checks before filtering.

The transfer process produced an account-specific file containing only
`U16088571` and dates on or after 20 January 2025. No other client account was
uploaded. The filtered file was uploaded to the non-public, server-encrypted
`input-snapshots` container and its local temporary copy was removed after the
successful import.

Transfer SHA-256:
`63fa602e47e4df9518f4294107b49ef2c75c8d9ddf8ebb791b4179309c95fd14`

## Import and reconciliation

The first manual import execution failed because the code called PostgreSQL
`executemany` on the connection instead of a cursor. The job had retry limit zero,
so it did not retry automatically. The issue was corrected, all tests passed, a
new commit-tagged image was built, and one controlled retry succeeded.

The successful import is ingestion run 2. It imported 39,520 rows including 87
referenced instruments. Independent read-back verification returned `PASS` with
no failures:

| Table | Rows | First date | Last date |
| --- | ---: | --- | --- |
| Accounts | 1 | — | — |
| NAV snapshots | 435 | 2025-01-20 | 2026-09-18 |
| Position snapshots | 17,788 | 2025-01-21 | 2026-09-18 |
| Cash snapshots | 433 | 2025-01-20 | 2026-09-18 |
| Trades | 205 | 2025-01-21 | 2026-09-08 |
| Cash transactions | 639 | 2025-02-05 | 2026-09-17 |
| Corporate actions | 3 | 2025-10-30 | 2026-06-12 |
| FX rates | 19,929 | 2025-01-20 | 2026-09-18 |
| FX transactions | 0 | — | — |

The independent verification also confirmed:

- the account set is exactly `U16088571`;
- both migrations are recorded;
- no required platform table is missing;
- all tenant-specific platform tables force row-level security;
- the successful import reconciliation is stored in PostgreSQL.

The failed first execution and failed ingestion run are retained as an audit
record. They do not affect the imported portfolio data.

## Private staging portal

The deployed Container App is `ibkrv2stg-web`. It provides:

- U16088571 overview and data-freshness information;
- daily NAV history from the approved start date;
- a simple NAV chart;
- latest holdings;
- account-scoped JSON endpoints;
- application-level allow-list checks for both the approved email and the signed-in
  user's Entra object ID.

The portal uses PostgreSQL role `ibkr_stg_web`, not the ingestion credential. The
role has `SELECT` permission only and defaults every transaction to read-only. A
live internal test successfully read one account and 435 NAV observations. A live
`UPDATE` test was rejected with `ReadOnlySqlTransaction`.

The web managed identity can:

1. pull images from the staging registry;
2. read blobs from staging storage;
3. read only the `database-web-url` Key Vault secret.

Its previous vault-wide secret permission was removed. It cannot read the database
administrator URL or the ingestion database credential.

Microsoft Entra application `4694ed1b-aa08-406a-9e3f-b46c57bd342d` is configured
as single-tenant. Container Apps authentication is enabled and unauthenticated
requests are configured to redirect to Microsoft sign-in. The client secret is
held by the Container Apps authentication configuration and was not written to
source control or displayed.

## Current safety state and browser acceptance

The user explicitly approved external ingress. On 22 September 2026 the portal
was enabled at:

`https://ibkrv2stg-web.purplerock-8cff24a2.eastasia.azurecontainerapps.io/`

The exact `/.auth/login/aad/callback` URL is registered with the single-tenant
Entra application. Ingress is external but HTTPS-only (`allowInsecure=false`),
minimum replicas remain zero and maximum replicas remain one. Unauthenticated
browser requests redirect to Microsoft sign-in.

Live browser acceptance as `anthony.ying@wealthryse.ai` passed. The portal:

- displayed the signed-in identity and account `U16088571`;
- displayed USD reporting from 20 January 2025;
- loaded the latest NAV of USD 124,474.26 as of 18 September 2026;
- loaded 435 NAV observations, the NAV-history chart and 44 latest positions;
- displayed the expected holdings table, beginning with AAPL, MU and CIEN.

The application allow-list remains restricted to the pilot user. Employee access
has not been enabled. A separate denied-user test is still required before any
future multi-user release.

## Deterministic benchmark foundation

The next implementation stage added calculation version
`benchmark-comparison-v1`. It:

- aligns portfolio and benchmark observations on exact common dates;
- normalizes both paths to 100 on the first common valid observation;
- adjusts portfolio returns for external cash flows using a Modified Dietz
  mid-period assumption;
- requires currency conversion to the reporting currency before comparison;
- calculates total and annualized returns, volatility, drawdown, beta,
  correlation, tracking error, information ratio, relative win rate, and upside
  and downside capture;
- fails publication for an unapproved licence, the wrong benchmark convention,
  intraday observations, stale data, insufficient history, invalid values or
  returns beyond review thresholds.

Migration `003_pilot_benchmark_contract.sql` records U16088571's primary
benchmark as the USD Nasdaq-100 Total Return Index (`XNDX`) from 20 January 2025.
Its licence remains deliberately `pending`, its provider is blank, and no
benchmark levels are loaded. The portal displays this blocked state rather than
substituting an unlicensed or different index.

The staging release used image `ibkr-platform:7d82c29`. ACR build `ck5`
completed successfully with digest
`sha256:40754be577f1f9d06c159f9cb152d03e7c9014ea52a420da5f660d1dd840b174`.
Bootstrap execution `ibkrv2stg-bootstrap-5xoqz9t` applied migration 003 and
verification execution `ibkrv2stg-verify-u16088571-f580rop` returned `PASS`.
The verifier confirmed the three migration checksums, exact account set, all
existing source row counts and coverage, 14 forced-RLS tables, and the pending
XNDX contract.

Portal revision `ibkrv2stg-web--0000003` became healthy. Live browser acceptance
confirmed that the original NAV and holdings still load and that the new XNDX
panel states: licence pending, zero official observations, and comparison
publication blocked until a licensed provider and validated closes are present.

## Provisional yfinance benchmark feed

The user approved Yahoo Finance through `yfinance` for development comparisons
until Bloomberg is integrated. A live source check showed that Yahoo does not
return the official Nasdaq-100 Total Return Index symbol `^XNDX`. Staging therefore
uses QQQ adjusted close as an explicitly labelled total-return development proxy.
This avoids silently substituting the Nasdaq-100 price index while preserving a
dividend-adjusted development series. It remains an ETF proxy, not official XNDX.

Migration `004_provisional_benchmark_feed.sql` adds the
`provisional_close` observation status and marks the proxy provider and licence as
restricted. The scheduled job `ibkrv2stg-benchmark-daily`:

- runs only when both the staging environment and unlicensed-data opt-in are set;
- retrieves QQQ adjusted close from 20 January 2025 through the latest completed
  US market date;
- records source, fetch time, digest and warning status;
- builds cash-flow-adjusted, common-date comparison rows and deterministic metrics;
- stops without materializing results if any substantive quality check fails;
- uses zero platform retries and safely backfills missed dates on the next run;
- never marks the feed publishable.

The portal labels the source as development-only, shows the proxy comparison and
keeps client publication disabled. Bloomberg must replace this feed and pass the
official-close and licence gates before any client-facing output is enabled.
The job is scheduled for 08:00 HKT daily, safely after the configured 18:30 New
York completion cutoff in both daylight-saving and standard time.

Release `624c8ef` was built by ACR run `ck6` and deployed only to staging.
Bootstrap execution `ibkrv2stg-bootstrap-jxo1oz3` applied migration 004.
Benchmark execution `ibkrv2stg-benchmark-daily-xi4571v` succeeded and stored 419
proxy observations from 21 January 2025 through 21 September 2026. It produced
418 aligned comparison rows through the latest available portfolio date,
18 September 2026. The result was `warn`, as designed, solely because the feed is
provisional/restricted and 20 January 2025 was a US market holiday.

Independent verifier execution `ibkrv2stg-verify-u16088571-5y3tr2p` returned
`PASS` with no failures. It confirmed all four migration checksums, exact pilot
source counts and coverage, 14 forced-RLS tables, the restricted QQQ proxy
contract, 419 benchmark rows and 418 comparison rows. A final wording-only image,
`ibkr-platform:f5f540e` from ACR build `ck7`, replaced all remaining official-index
wording in the proxy chart and metric label. Portal revision
`ibkrv2stg-web--0000005` became healthy and a signed-in Chrome check confirmed:

- the approved `anthony.ying@wealthryse.ai` identity remained signed in;
- the existing NAV, date, observation count, positions and holdings were intact;
- the source, restricted licence, provisional dates and no-publication warning
  were visible;
- the rebased comparison chart and eight deterministic comparison metrics loaded.

The production `ibkr-v11-daily` job remained on its pinned digest, `0 8 * * *`
UTC (16:00 HKT), retry limit zero and parallelism one. No production code,
schedule, credential or data was changed.

## Primary benchmark changed to S&P 500 Total Return

The user subsequently replaced Nasdaq-100/QQQ with the S&P 500 as U16088571's
primary benchmark. The established total-return convention was retained. A live
yfinance check confirmed that the exact S&P 500 Total Return Index symbol
`^SP500TR` is available, so no ETF or price-index proxy is required.

Release `2d0d014` was built by ACR run `ck8`. Migration
`005_sp500_total_return_primary.sql`:

- registered `SP500TR` / `^SP500TR` as the active gross-total-return benchmark;
- mapped it to U16088571 from 20 January 2025 in USD;
- retired the old XNDX definition and removed its primary mapping;
- retained the old XNDX prices and comparisons only as inactive audit history.

Bootstrap execution `ibkrv2stg-bootstrap-7fx610z` applied the migration.
Benchmark execution `ibkrv2stg-benchmark-daily-9j79f4d` succeeded with 419 index
observations from 21 January 2025 through 21 September 2026 and 418 aligned
comparison rows through 18 September 2026. The recalculated period metrics were:

- portfolio total return: 23.70%;
- S&P 500 Total Return Index return: 29.10%;
- excess total return: -5.40%;
- tracking error: 13.91%;
- information ratio: -0.07;
- beta: 1.23;
- correlation: 0.84;
- relative win rate: 51.80%.

Verifier execution `ibkrv2stg-verify-u16088571-ze5j63e` returned `PASS` with no
failures. It confirmed migration 005, the SP500TR contract, the inactive XNDX
definition with zero primary mappings, exact source counts, 14 forced-RLS tables,
419 benchmark observations and 418 comparison rows. Portal revision
`ibkrv2stg-web--0000006` became healthy. A signed-in Chrome check confirmed that
the primary benchmark, warning, chart legend and all comparison metrics now show
S&P 500 Total Return, with no QQQ/XNDX benchmark presented to the user.

## Primary and secondary benchmarks changed to price indexes

The user subsequently selected the S&P 500 Price Index as U16088571's primary
benchmark and the NASDAQ Composite Price Index as its selectable secondary
benchmark. Release `9628ae1` was built by ACR run `ck9`. Migration
`006_price_index_benchmarks.sql`:

- registered `GSPC` / `^GSPC` as the active primary price-index contract;
- registered `IXIC` / `^IXIC` as the active secondary price-index contract;
- mapped both to U16088571 from 20 January 2025 in USD;
- retired SP500TR and preserved its historical data as inactive audit history.

Bootstrap execution `ibkrv2stg-bootstrap-d8ziu9e` applied migration 006.
Benchmark execution `ibkrv2stg-benchmark-daily-vomxcu1` succeeded. Each index
has 419 provisional closing observations from 21 January 2025 through
21 September 2026 and 418 aligned comparison rows through 18 September 2026.
The S&P 500 price-index comparison produced 26.47% benchmark total return and
-2.77% excess return. The NASDAQ Composite comparison produced 34.25% benchmark
total return and -10.54% excess return. Both remain restricted, provisional and
blocked from client publication.

Verifier execution `ibkrv2stg-verify-u16088571-oao6w0g` returned `PASS` with no
failures. It confirmed all six migrations, both benchmark mappings and
conventions, the two 419-row price series, the two 418-row comparisons, exact
pilot source counts, 14 forced-RLS tables, and clean retirement of SP500TR and
XNDX.

The first portal release exposed a PostgreSQL optional-parameter typing error
during the signed-in browser test. Release `cfdfb1f`, built by ACR run `cka`,
added an explicit text cast and a regression assertion. Portal revision
`ibkrv2stg-web--0000008` became healthy. A signed-in Chrome test as
`anthony.ying@wealthryse.ai` confirmed that:

- the default selection is Primary — S&P 500 Price Index (`^GSPC`);
- selecting Secondary — NASDAQ Composite Price Index (`^IXIC`) refreshes the
  title, warning, metrics, legend and graph;
- switching back restores the S&P 500 price-index comparison;
- the existing NAV, holdings and as-of information remain intact;
- no revision-0000008 errors were recorded after the test.

## Interactive chart axes and exact-value tooltips

The performance-comparison and NAV charts were upgraded with visible X and Y
axes, five dated X-axis ticks, five numeric Y-axis ticks, grid lines and explicit
axis titles. Cursor movement selects the nearest observation, draws a vertical
guide and point markers, and displays the exact observation date and curve
values to as many as six decimal places. The NAV tooltip also identifies USD.

Release `6e9d9a5` was built by ACR run `ckd` after signed-in visual checks caught
and corrected clipped tick labels and axis-title spacing. Portal revision
`ibkrv2stg-web--0000011` became healthy. A final signed-in Chrome check confirmed
fully visible axes and dates on both charts, an exact two-curve tooltip on the
recalculated comparison, and an exact NAV tooltip (for example, 8 December 2025,
USD 103,707.555145). The calculation data and APIs were not changed.

## Verification and production isolation

- 62 local automated tests pass.
- all staging Bicep templates compile.
- the staging portal and authentication sidecar started with zero restarts.
- live Azure read-back confirmed provisioning state `Succeeded`, external ingress
  enabled, insecure HTTP disabled, Entra authentication enabled and unauthenticated
  action `RedirectToLoginPage`.
- the temporary user blob-write role was removed after upload.
- the production job remains `0 8 * * *` UTC (16:00 HKT), retry limit zero,
  parallelism one and provisioning state `Succeeded`.
- the 22 September production execution succeeded from 08:00:00 to 08:02:30 UTC.

## Portfolio-wide as-of date control

Release `abf1c31` was built by ACR run `cke` as image digest
`sha256:c32fee21207f5ee38a714d8711ced1569ec521ab525421250d410b4461de0a68`
and deployed only to staging. Portal revision `ibkrv2stg-web--0000012` became
healthy and received 100% of staging traffic; the superseded revision was
deactivated.

The portal now defaults to the latest validated portfolio date and offers every
available U16088571 NAV date from 20 January 2025 onward in an accessible `As
of` selector. The selected date is enforced server-side for the NAV summary,
observation count, NAV history, holdings snapshot, comparison series and all
derived benchmark metrics. API date inputs are ISO-validated and invalid dates
fail with HTTP 400.

A signed-in Chrome acceptance test as `anthony.ying@wealthryse.ai` confirmed:

- the default remained 18 September 2026 with USD 124,474.26 NAV, 435
  observations, 44 positions and holdings dated 18 September 2026;
- selecting 30 June 2026 consistently changed the page to USD 131,632.59 NAV,
  377 observations, 43 positions and holdings dated 30 June 2026;
- S&P 500 comparison metrics were recalculated only through 30 June 2026;
- switching to the NASDAQ Composite at the historical date retained the selected
  date and recalculated the secondary comparison;
- returning to 18 September 2026 and the primary S&P 500 restored the default
  view;
- revision `0000012` logs contained a clean application start and no runtime
  errors after the live tests.

The production `ibkr-v11-daily` job was not modified. Final Azure read-back
confirmed its pinned image digest, `0 8 * * *` UTC schedule (16:00 HKT) and
retry limit zero remained unchanged.

## Cash, risk, exports and system-health views

Release `8b90297` was built by ACR run `ckf` as image digest
`sha256:8993ea4e3111d7da683fad837980c8c8cac34b7351a9e886185abb56f1f46c2a`
and deployed only to staging. Portal revision `ibkrv2stg-web--0000013` became
healthy and received 100% of staging traffic; the superseded revision was
deactivated.

The account-scoped portal now adds:

- current cash balances and trailing 30-calendar-day cash activity through the
  selected as-of date;
- deterministic total return, annualized return, annualized volatility,
  maximum drawdown, daily VaR/CVaR, win rate and BLUE daily-return estimates;
- risk-free-rate and Sharpe fields that fail closed as `Unavailable` when no
  validated USD risk-free observation exists on or before the selected date;
- as-of-bounded performance, holdings and cash-activity CSV exports with
  spreadsheet-formula injection protection;
- a system-health view containing the environment, run status, latest portfolio
  date and latest successful run time.

The risk engine consumes the already validated, cash-flow-adjusted portfolio
returns on common primary-benchmark trading dates and identifies calculation
version `benchmark-comparison-v1`. It does not allow the language model or the
browser to calculate financial figures.

A signed-in Chrome acceptance test confirmed the latest view and a historical
30 June 2026 view. On the historical selection, NAV, risk period, cash snapshot,
holdings snapshot and all export URLs consistently used 30 June 2026. A real
performance-CSV browser download completed successfully. The page was restored
to 18 September 2026 after testing. Revision `0000013` logs contained a clean
application start and no runtime errors.

Final Azure read-back confirmed the production `ibkr-v11-daily` image, `0 8 * *
*` UTC schedule (16:00 HKT), parallelism one and retry limit zero were unchanged.

## Official Treasury risk-free rates and Sharpe

Release `1ce1190` was built by successful ACR run `ckh` as image digest
`sha256:cbc87e04a8824ef5dcfb4c4282aa2a43e8defd3705cfcec304059b85b2de684b`.
Portal revision `ibkrv2stg-web--0000014` became healthy and received 100% of
staging traffic.

The release adds an independent staging job named
`ibkrv2stg-risk-free-daily`. It:

- uses the official U.S. Treasury Daily Treasury Par Yield Curve XML feed;
- loads the 3-month tenor as series `UST-PAR-3M`;
- is idempotent, validates dates and plausible numeric ranges, and preserves
  corrected official observations through PostgreSQL upsert;
- runs at `0 23 * * *` UTC (07:00 HKT) with parallelism one and retry limit zero;
- uses only the staging ingestion identity and staging database credential;
- contains no IBKR Flex credential and never calls IBKR.

Manual execution `ibkrv2stg-risk-free-daily-3w7slmx` succeeded and loaded 418
validated observations from 21 January 2025 through 21 September 2026. The
latest loaded annual rate was 4.17%.

Signed-in Chrome acceptance testing confirmed:

- at the latest portfolio date, 18 September 2026, the page displayed the
  applicable 18 September Treasury observation of 4.14% and Sharpe of 0.48;
- selecting 30 June 2026 displayed the applicable 30 June observation of 3.87%
  and recalculated Sharpe of 0.74;
- the page disclosed the source, tenor and exact observation date;
- restoring 18 September 2026 restored the latest view.

Final Azure read-back confirmed Entra authentication still redirects
unauthenticated users to sign-in, revision `0000014` is healthy, and production
`ibkr-v11-daily` remains on its prior pinned digest with the unchanged 16:00 HKT
schedule and retry limit zero.

## Expected Tail Loss and expanded drawdown analytics

Release `40910e2` was built by successful ACR run `ckj` as image
`ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform:40910e2`, digest
`sha256:e212fd63aa0ed559aafbd20bea0504d5e8f1ce36a56a461af2607686152c8925`.
It was deployed only to staging. Portal revision
`ibkrv2stg-web--0000015` became active and healthy with one running replica.

The release replaces the single maximum-drawdown card with:

- Expected Maximum Drawdown (1 Year), the mean of 5,000 deterministic
  five-session moving-block bootstrap maximum drawdowns over 252-session paths;
- Actual Maximum Drawdown (1 Year), calculated from the latest 252 validated
  return observations;
- Tail Maximum Drawdown (1 Year), the mean of the worst 5% of simulated
  one-year maximum drawdowns; and
- Crisis Maximum Drawdown for 15 September 2008 through 9 March 2009. Release
  `40910e2` initially left this unavailable because U16088571 starts in January
  2025; that definition was subsequently superseded by the current-holdings
  replay described below.

Daily Expected Tail Loss is the existing historical 95% Expected Shortfall
measure, now labelled explicitly. The portal also ranks the top five loss
contributors and bottom five offsets. Attribution is limited to carried USD
positions with unchanged aggregate quantity and currency across adjacent
validated dates; the disclosed residual reconciles cash, fees, FX, trades and
unmatched positions rather than forcing those effects into securities.

Signed-in Chrome acceptance testing as `anthony.ying@wealthryse.ai` confirmed:

- the latest 18 September 2026 view displayed 3.44% Daily Expected Tail Loss,
  -19.81% expected one-year maximum drawdown, -18.96% actual one-year maximum
  drawdown and -38.52% tail one-year maximum drawdown;
- the top five displayed contributors were MU 0.35%, AAPL 0.32%, IONQ 0.31%,
  HL 0.27% and CDE 0.16%, while five loss offsets were shown separately;
- the 21 portfolio tail days were disclosed along with the attribution residual;
- selecting 30 June 2026 recalculated the measures to 3.42%, -18.55%, -14.50%
  and -36.90%, respectively, and rebuilt both contributor rankings from 19 tail
  days; and
- restoring 18 September 2026 restored the latest values, Treasury observation
  and contributor tables.

All 69 local automated tests pass, including deterministic simulation,
252-observation lookback, Expected Tail Loss semantics, contributor
reconciliation/ranking and website-label regressions.

Final Azure read-back confirmed revision `0000015` remained active and healthy
with one running replica after the browser tests; its logs contained a clean
application start and no runtime errors. Entra authentication remained enabled
with unauthenticated requests redirected to sign-in. Production
`ibkr-v11-daily` remained on its prior pinned image with schedule `0 8 * * *`
UTC (16:00 HKT), parallelism one and retry limit zero. The independent staging
Treasury job remained `Succeeded` on schedule `0 23 * * *` UTC (07:00 HKT) with
retry limit zero.

## Current-holdings 2008–2009 crisis replay

The user clarified that Crisis Maximum Drawdown is a hypothetical replay of
today's holdings, not realized account history. Release `78a64e3`, packaged in
the BuildKit-independent image release `4731ccf`, implements that definition.
ACR run `ckm` succeeded and produced image digest
`sha256:b5b9eca7de40d948dca38df118236ae17a61ba31b7c3d40fb6c78356e2c8c86d`.

Migration `007_crisis_holdings_replay.sql` adds tenant-isolated, forced-RLS
component and daily replay tables. The staging-only yfinance job now runs the
replay after refreshing the provisional index comparisons. For each latest
holding it:

- uses the holding's own complete adjusted-close history when available;
- otherwise substitutes its recorded sector ETF proxy, with SPY as the explicit
  broad-market fallback;
- fixes opening units at the holding's latest market-value-to-NAV weight;
- keeps residual cash flat and non-interest-bearing; and
- performs no rebalancing or partial direct/proxy price splicing.

Bootstrap execution `ibkrv2stg-bootstrap-shku3mq` applied migration 007.
Materialization execution `ibkrv2stg-benchmark-daily-tbjk9nr` succeeded with:

- holdings as of 18 September 2026;
- 44 components: 31 direct histories and 13 recorded proxies;
- 121 crisis-period observations from 15 September 2008 through 9 March 2009;
- residual cash weight of -4.097%, preserving the account's leverage; and
- Crisis Maximum Drawdown of -53.8848%.

Verifier execution `ibkrv2stg-verify-u16088571-c1jd0st` returned `PASS`, with
all seven migrations, no missing platform tables, 16 forced-RLS tables, no RLS
failures and unchanged pilot source counts. Portal revision
`ibkrv2stg-web--0000016` became active and healthy.

Signed-in Chrome acceptance testing confirmed the Risk Analytics card displays
-53.88% and discloses the holdings date, crisis window, 31/13 direct/proxy split,
flat -4.1% cash weight, fixed-unit/no-rebalancing convention, provider and
publication restriction. Selecting 30 June 2026 recalculated the ordinary
as-of risk metrics but correctly retained the same latest-holdings crisis replay;
the page was then restored to 18 September 2026.

All 72 local automated tests pass. The replay fails closed for incomplete price
inputs and remains restricted to staging because Yahoo Finance via yfinance is
not approved for client publication.

Final Azure read-back confirmed revision `0000016` active and healthy with one
running replica and a clean post-test application log. Entra authentication
remained enabled with unauthenticated requests redirected to sign-in. The
staging benchmark/crisis job remained `Succeeded` on `0 0 * * *` UTC (08:00
HKT), parallelism one and retry limit zero. Production `ibkr-v11-daily` retained
its prior immutable image, `0 8 * * *` UTC (16:00 HKT) schedule, parallelism one
and retry limit zero.

## Phase 4 security acceptance release

The Phase 4 access-control, session and audit acceptance release was deployed to
staging on 22 September 2026. Source commit `1fefade` produced ACR image
`ibkr-platform:1fefade` with digest
`sha256:c362470a588fafc489bd4c432c77978fa40d3268650458de43ceb7bcb2837bb2`.
Container App revision `ibkrv2stg-web--0000017` became healthy, provisioned and
received 100% of staging traffic.

Azure deployment `ibkrv2-staging-auth-security-20260922` applied the following
session policy without changing the app registration or granting permissions:

- fixed Entra authentication-cookie lifetime: eight hours;
- nonce validation enabled with a five-minute nonce lifetime;
- HTTPS required;
- unauthenticated requests handled by Entra authentication; and
- Easy Auth token store disabled.

The application still requires both the allow-listed email
`anthony.ying@wealthryse.ai` and the pre-existing allow-listed Entra object ID.
The database URL used by the portal remains the read-only website credential.
No application route received arbitrary SQL, write access or a database
administrator credential.

The release added structured request audit events to Container App logs. Each
event records UTC time, path without query string, HTTP method and status,
duration, outcome and a generated correlation ID. Email and object-ID claims
are SHA-256 digests; raw identity values, cookies, tokens and request query
strings are not logged. Responses include the correlation ID, `no-store`,
content-sniffing prevention, no-referrer and frame-embedding protections.

Acceptance evidence:

- all 78 local automated tests passed;
- the Bicep authentication definition compiled without warnings;
- the infrastructure what-if showed only the staging portal authentication
  child resource as deployable and ignored production resources;
- a public request carrying forged `x-ms-client-principal-*` headers received
  `401 Unauthorized` from Entra authentication;
- a live Chrome reload as `anthony.ying@wealthryse.ai` displayed U16088571 NAV
  `124,474.26 USD`, risk-free rate `4.14%`, Sharpe ratio `0.48` and staging
  system status `pass`;
- the corresponding portal page and API calls emitted successful structured
  audit events with unique correlation IDs and hashed identity fields;
- unit tests proved wrong identity claims receive application-level `403` and
  an empty allow-list fails closed with `503`;
- the eight-hour expiry was verified from Azure configuration and source tests.
  It was not tested by waiting eight real hours; and
- the production job remained on `0 8 * * *` UTC (16:00 HKT), retry limit zero,
  parallelism one and its pinned production image. Its 20, 21 and 22 September
  scheduled runs all succeeded.

A real browser sign-in by an unapproved second employee has intentionally not
been attempted because no second identity is currently authorized or required.
That test remains mandatory before employee access is expanded. It does not
block the approved single-user staging pilot.

## Phase 5 deterministic analyst-tool foundation

The first Phase 5 release was deployed to staging on 22 September 2026. Source
commit `6964b6d` produced ACR image `ibkr-platform:6964b6d` with digest
`sha256:01e587166367e3570648ff255b60be7e556606f8b50803a0f9a9f1cd2efa9498`.
Container App revision `ibkrv2stg-web--0000018` became healthy, provisioned and
received 100% of staging traffic.

The release adds a fixed registry of seven tools:

- `get_portfolio_summary`;
- `get_performance_series`;
- `get_holdings`;
- `compare_with_benchmarks`;
- `get_risk_metrics`;
- `get_data_freshness`; and
- `get_sector_allocation`.

Every tool accepts a required subject identifier and an optional as-of date.
Only `U16088571` is authorized for the pilot. Benchmark and holdings tools accept
only their explicit additional parameters. Unexpected fields, unknown tools and
unapproved subject identifiers are rejected before database access. The module
contains static `SELECT` statements only and has no process execution,
arbitrary-SQL, trading, secret, permission-change or self-modification
capability. It continues using the existing read-only `ibkr_stg_web` database
role.

Results include the effective as-of date, quality status, calculation version
where applicable and structured citations naming the deterministic source
tables and dates. Each call emits an `AGENT_TOOL_AUDIT` event containing a
correlation ID, tool and subject, request/response SHA-256 values and hashed
actor identity. Request bodies, database results, raw identity claims, cookies
and tokens are not written to the audit event.

The sector tool deliberately returns `available: false` until a validated and
licensed sector classification covers every holding. It does not ask a language
model to guess classifications. Provisional Yahoo Finance benchmark results
remain `warn` and non-publishable.

Acceptance evidence:

- all 85 automated tests passed;
- the deployed seven-tool live run through the read-only PostgreSQL role returned
  the expected `pass` or `warn` state for every tool;
- the live summary returned U16088571 NAV `124474.258898535` USD as of
  18 September 2026;
- the live risk tool returned Sharpe `0.48120042403438024` and the validated
  18 September 2026 risk-free observation;
- the authenticated HTTP tool endpoint returned status 200, a generated
  correlation ID and the same summary NAV;
- the dashboard displayed `7 deterministic tools ready`, while clearly stating
  that narrative AI remains disabled pending region approval;
- the matching structured tool audit event appeared in Container App logs with
  hashed identity and request/response values; and
- production retained its pinned image, `0 8 * * *` UTC (16:00 HKT) schedule,
  retry limit zero and parallelism one.

No Microsoft Foundry, Azure OpenAI or other model resource was created. Azure
still contains no Cognitive Services or Machine Learning resource in the shared
resource group. Current Microsoft documentation was rechecked: Southeast Asia
supports Agent Service, Responses API and private networking, while East Asia is
not listed for Agent Service. Explicit approval for processing selected
portfolio facts in Southeast Asia is still required before the model-backed
narrative layer can be deployed.

## Phase 5 regional AI boundary and privacy gate

Later on 22 September 2026, the user approved sending only structured
`U16088571` NAV, performance, holdings, benchmark and risk results to a
regional Microsoft Foundry deployment in Southeast Asia. Raw IBKR/Flex files,
Flex credentials, database credentials and personal information remain
explicitly excluded.

The no-model baseline above was superseded only at the resource-boundary level:

- Azure OpenAI/Foundry account `ibkrv2stg-ai-auhecwc4itaoo` was created in
  `southeastasia`;
- local key authentication is disabled;
- the web managed identity received only `Cognitive Services OpenAI User` at
  that account scope; and
- no model deployment was created, so no data was transmitted.

The live subscription catalog agreed with current Microsoft documentation: no
regional `Standard` text-generation SKU is available in Southeast Asia. The
catalog exposed regional provisioned text models with a minimum 25 PTUs, which
is outside the KRW 120,000 staging ceiling. Global and Data Zone SKUs were not
substituted because they have a different processing boundary.

The application-side narrative layer is nevertheless ready and fail-closed. It
supports four fixed analysis types, strips account alias and operational fields,
sends no model tools, uses stateless strict-schema output, validates citations
against supplied sources and audits only hashes, correlation ID, region,
deployment and token counts. All 92 tests and both Bicep compilations passed.

A pre-deployment what-if also detected that a full Container App template update
would remove the existing Easy Auth secret if it were not supplied and would
change the internal portfolio tenant if the wrong parameter were used. The
release was stopped before either change occurred. The source template now
requires the existing Entra secret as a secure parameter, and the release uses
an in-place revision update that preserves the current auth configuration and
the internal tenant `00000000-0000-0000-0000-000000000001`.

Staging application commit `7427137` produced image
`ibkr-platform:7427137` with immutable digest
`sha256:8124f7d5d579c4c936e582f1a6214648cdc9482658937504617393e9a49ec1ed`.
The template-preservation fix is commit `8440113`. Container App revision
`ibkrv2stg-web--0000019` became healthy and provisioned with 100% of staging
traffic; revision 18 remains available at zero traffic for rollback.

Post-deployment acceptance evidence:

- Entra Easy Auth remained enabled with the same application, issuer and
  `microsoft-provider-authentication-secret` setting;
- an external unauthenticated request received a 302 redirect to the correct
  tenant's Microsoft sign-in endpoint;
- all original database, tenant, account and two-factor allow-list environment
  settings remained unchanged;
- the internal health endpoint returned 200;
- the agent catalog returned seven deterministic tools, configured region
  `southeastasia`, and `enabled: false`;
- a narrative request returned fail-closed HTTP 503 before model or database
  activity;
- the existing portfolio summary endpoint still returned U16088571 NAV
  `124474.258898535` USD as of 18 September 2026;
- revision logs showed clean startup and only the expected audited requests;
- the Foundry account still had zero model deployments; and
- production remained on image digest
  `sha256:0d0eb9105de73b9339bcdbc58a5c05069177f010b21dd13c9bd2f77ff0a7ca91`,
  schedule `0 8 * * *` UTC (16:00 HKT) and retry limit zero.

## APAC Data Zone model activation and first grounded test

The user subsequently approved APAC Data Zone processing and an economical
pay-as-you-go `gpt-5.6-sol` deployment. Deployment
`gpt-5-6-sol-apac` was created in the existing keyless Southeast Asia account
with model version `2026-07-09`, SKU `DataZoneStandard` and capacity 10. The
account continues to have local authentication disabled and the web identity
continues to hold only `Cognitive Services OpenAI User` at the account scope.

The first activation preflight detected that the portal described the resource
region as the processing region. No model request was made from that revision.
The release was fail-closed, and commit `8468a07` separated the Southeast Asia
resource location from the approved `apac-data-zone` processing scope in code,
configuration, API output, UI disclosure and audit evidence. Image
`ibkr-platform:8468a07` had immutable digest
`sha256:3b1c854d28d9a455dde2b07087f9c2ec8d0abaf93e8cc1c45f1dcc92873fd74b`.

The initial controlled model request reached the service but returned
`invalid_model_output`; every deterministic tool had succeeded and no narrative
was published. Commit `9383450` added explicit fail-closed handling for
incomplete/refused responses, allowed sufficient output space for reasoning
tokens and capped narrative length. All 93 tests and 21 subtests passed. ACR
run `ckt` produced immutable digest
`sha256:967ecd50c41f29e1a592afb36728b2da151d980fd917456a9b528862c9fa4661`.
Staging revision `ibkrv2stg-web--0000024` became healthy with 100% of staging
traffic while Entra Easy Auth and HTTPS-only ingress remained enabled.

The controlled retry succeeded for U16088571, analysis type
`portfolio_overview`, as of 18 September 2026, using primary benchmark `GSPC`.
Correlation ID `9bcd2a6e-1a05-45fa-8e83-3853c3e7c899` returned HTTP 200 and a
strict-schema narrative whose citations were limited to the supplied grounding
set. The response correctly preserved the validated NAV, position count,
performance, benchmark, risk and data-quality limitations. The audit event
recorded 1,753 input tokens, 797 output tokens (including 118 reasoning tokens),
2,550 total tokens, hashes, deployment, resource region and processing scope;
it did not record prompt or response bodies.

Additional acceptance evidence:

- the agent catalog reported `enabled: true`, resource region
  `southeastasia`, processing scope `apac-data-zone`, local keys disabled and
  managed-identity-only authentication;
- the existing portfolio summary endpoint still returned NAV
  `124474.258898535` USD as of 18 September 2026;
- a request for an unauthorized subject returned HTTP 403 before tool or model
  activity;
- the provisional yfinance S&P 500 comparison remained explicitly restricted
  and not publishable; and
- production remained on immutable image digest
  `sha256:0d0eb9105de73b9339bcdbc58a5c05069177f010b21dd13c9bd2f77ff0a7ca91`,
  schedule `0 8 * * *` UTC (16:00 HKT) and retry limit zero.

## Remaining functional work

- licensed Bloomberg S&P 500 Price Index and NASDAQ Composite Price Index
  sources to replace the provisional Yahoo Finance feeds;
- licensed benchmark and FX ingestion and scheduled materialization;
- Excel/PDF exports and richer multi-page navigation;
- factsheet and presentation generation with publication approval controls.

## Controlled staging administrator workflow

The next Phase 5 milestone was deployed on 22 September 2026 from commit
`7d3234f`. ACR build `cku` produced image `ibkr-platform:7d3234f` with immutable
digest
`sha256:67e2a516d395a75fe3be79a189b54706363ab2eb16e6e1f86cfcb61fc62f8bec`.
Container App revision `ibkrv2stg-web--0000025` became healthy and active.

The portal now exposes two and only two controlled operations:

- `refresh_risk_free_rate`, mapped to `ibkrv2stg-risk-free-daily`; and
- `refresh_provisional_benchmarks`, mapped to
  `ibkrv2stg-benchmark-daily`.

Both are hard-coded as staging-only. An action requires a ten-minute preview,
a SHA-256 payload digest, the same authenticated Entra object ID, the exact
displayed confirmation phrase and an atomic `pending` to `executing` claim.
Approvals expire, are single-use and cannot be replayed. The model is not given
either action as a tool. Production jobs, IBKR/Flex refreshes, arbitrary job
names, secret retrieval, role changes and code deployment remain unavailable.

Migration `008_controlled_admin_actions.sql` extended the audit record and the
bootstrap created the dedicated `ibkr_stg_approval` role. That role can read,
insert and update only `approved_actions`; the normal portal role remains
transaction-level read-only. The web identity received `Container Apps Jobs
Operator` only on the two exact staging job resources and `Key Vault Secrets
User` only on the exact `database-approval-url` secret. It has no corresponding
assignment on production.

Live acceptance evidence:

- a risk-free preview was created with approval ID `1` and payload digest
  `1f467cce4b603c10897325ada899f97de7b4f55547d779e1264aaf292745d087`;
- a wrong phrase failed closed with HTTP 400 `confirmation_mismatch`;
- the exact phrase started execution
  `ibkrv2stg-risk-free-daily-givdpbz` once;
- replay failed closed with HTTP 409 `approval_not_pending`;
- the execution succeeded and validated 419 U.S. Treasury observations, with
  latest observation 22 September 2026 and annual rate 4.16%;
- the approval row ended in `executed` with no failure code and the correct
  Azure execution reference;
- the approval-only role was denied access to `nav_snapshots` with
  `InsufficientPrivilege`;
- the normal web role was denied an approval update with
  `ReadOnlySqlTransaction`;
- application logs contained the preview, rejected phrase, approval, execution
  and rejected replay audit events without secret values; and
- browser verification showed `Approval workflow ready`, both fixed actions,
  and disabled confirmation/execution controls before preview.

The production daily job remained unchanged on digest
`sha256:0d0eb9105de73b9339bcdbc58a5c05069177f010b21dd13c9bd2f77ff0a7ca91`,
schedule `0 8 * * *` UTC (16:00 HKT) and retry limit zero.

## First frozen month-end reporting snapshot

Phase 6 began on 23 September 2026 with commit `609a1a5`. Migration
`009_reporting_snapshot_payload.sql` adds the canonical payload, publication
gate, benchmark contract and data-as-of fields to `reporting_snapshots`. A
database trigger rejects content changes after a snapshot enters `frozen` or
`approved` status while still allowing a separately authorized status
transition.

ACR build `ckv` succeeded with immutable digest
`sha256:e30b99d39115e6ecd5d478272739831f3f6b1685c8e3e724a945ea9cd79ec1e0`.
The staging bootstrap execution `ibkrv2stg-bootstrap-5r577rf` applied migration
009 successfully. The new `ibkrv2stg-report-snapshot` job is manual,
staging-only, single-replica, has retry limit zero and has no IBKR/Flex secret.
Commit `e414033` and deployment
`ibkrv2-staging-report-snapshot-digest-20260923` pin it to the immutable image
digest.

The first execution, `ibkrv2stg-report-snapshot-7sbyxyl`, froze the completed
reporting month ending 31 August 2026:

- snapshot ID: `541bb0ba-a802-5aa2-b8df-ecd0b373ac96`;
- reporting period: 20 January 2025 through 31 August 2026;
- data as of: 31 August 2026;
- reporting currency: USD;
- payload SHA-256:
  `211f9aea565eb576397b04ed3835b93834e000420660aa16fd98dbaf59906ed0`;
- quality status: `warn`; and
- publication allowed: `false`.

The frozen payload records three publication blockers: the provisional S&P 500
Price Index feed is not licensed for publication, the provisional NASDAQ
Composite Price Index feed is not licensed for publication, and validated
sector classifications are unavailable. These are explicit gates rather than
missing values or silent estimates.

Execution `ibkrv2stg-report-snapshot-q0puf0u` repeated the same operation. It
returned the same snapshot ID and hash with `inserted: false`, proving the
month-end operation is idempotent and did not create a duplicate. The expanded
local suite passed 106 tests and 26 subtests. Final Azure read-back confirmed
the staging portal remained healthy on revision `0000025` and production
remained unchanged on schedule `0 8 * * *` UTC (16:00 HKT), retry limit zero and
digest
`sha256:0d0eb9105de73b9339bcdbc58a5c05069177f010b21dd13c9bd2f77ff0a7ca91`.

## Provisional sector classification and refreshed reporting snapshot

On 23 September 2026, the user explicitly approved transmitting the current
U16088571 ticker symbols to Yahoo Finance for provisional sector
classification. Commit `5c11134` adds migration
`010_provisional_sector_classifications.sql`, a staging-only classification
job, restricted-source metadata, fail-closed sector aggregation and coverage
tests. The job sends only ticker symbols; it does not send the account number,
quantities, market values, NAV, personal information, credentials or raw IBKR
files. It stores selected classification fields and a response digest, not the
raw Yahoo response.

ACR build `ckw` succeeded with immutable digest
`sha256:ac365a6906b60b2d71c50a37a9fe1b40a82c42f4dc3ec9cbf0e9987f82b22d75`.
Bootstrap execution `ibkrv2stg-bootstrap-llkwye1` applied migration 010
successfully. Classification execution
`ibkrv2stg-sector-classification-6li9ulv` succeeded for position date
31 August 2026: 43 ticker symbols transmitted, 43 classified and zero
unclassified. The source is recorded as `Yahoo Finance search profile` with
`license_status=restricted`.

Deployment `ibkrv2-staging-report-snapshot-sector-20260923` pinned the manual
snapshot job to the same immutable digest. Execution
`ibkrv2stg-report-snapshot-9i1avin` created the superseding frozen snapshot:

- snapshot ID: `342e82f3-15b7-532b-83b5-663f1b3e6570`;
- reporting period: 20 January 2025 through 31 August 2026;
- data as of: 31 August 2026;
- payload SHA-256:
  `770487f5ea92d714f54ad062accd41a02da80b087c66278517cce4b038d83eec`;
- quality status: `warn`; and
- publication allowed: `false`.

Sector allocation is now present. The remaining publication blockers are the
restricted Yahoo primary benchmark, secondary benchmark and sector
classification sources. Execution `ibkrv2stg-report-snapshot-on11wm0` returned
the same snapshot ID and hash with `inserted: false`, proving idempotency. The
expanded local suite passed 112 tests and 26 subtests.

## Private snapshot delivery and first reconciled report drafts

Phase 6 continued on 23 September 2026 with three small, reviewed releases.
Commit `a1e7859` added an authenticated frozen-snapshot endpoint, commit
`201790f` made the snapshot inspection compatible with inline JSON payloads, and
commit `5edb751` added a `latest` snapshot alias and the portal download link.
ACR build `ck10` produced immutable digest
`sha256:b212221dd90c0ca94c9a59c75926a51a3e28ec60d2d680ae236b2baf845808d5`.
Staging web revision `ibkrv2stg-web--0000028` became healthy and active. The
endpoint verifies the stored payload SHA-256 before returning a frozen or
approved snapshot and remains tenant-, account- and Entra-allow-list scoped.

Commit `17f4223` extended the manual reporting-snapshot job to upload the same
integrity-verified JSON to the private `report-drafts` container. The first
execution failed closed because the user-assigned publisher identity was not
selected inside the job. The failed execution was stopped; no automatic retry
occurred. Commit `ce8b8d6` supplied the exact managed-identity client ID through
`AZURE_CLIENT_ID`. ACR build `ck11` produced immutable digest
`sha256:43e4f93e80e5521de275bd2877fb103e4157c46719a26958edb9ddd8a6709987`,
and deployment `ibkrv2-staging-report-publish-identity-20260923` applied it.
Execution `ibkrv2stg-report-snapshot-ru0oxlx` then succeeded and published:

`snapshots/U16088571/2026-08-31/342e82f3-15b7-532b-83b5-663f1b3e6570.json`

The blob remains private. The already approved user
`anthony.ying@wealthryse.ai` received `Storage Blob Data Reader` only at the
`report-drafts` container scope. The locally retrieved bytes matched the frozen
snapshot SHA-256 exactly:
`770487f5ea92d714f54ad062accd41a02da80b087c66278517cce4b038d83eec`.

The expanded automated suite passed 115 tests and 26 subtests. The source
snapshot then produced two internal-only draft artifacts:

| Artifact | Size | SHA-256 |
| --- | ---: | --- |
| U16088571 factsheet PDF | 593,513 bytes | `c1e518ca7b806048ab6c0a97b62c20d7e9aba9ad0e46d93d1c43e37c887e9ebc` |
| U16088571 presentation PPTX | 892,096 bytes | `f535796866877e24c54b0f3f3c46b5f90b298d271e47872522c125584379bdc4` |

The factsheet retains the approved two-page PDF template, displays the verified
holdings, sector allocation, performance and risk figures, and is visibly
marked as an internal development draft. Both pages were rendered at original
resolution and visually reconciled.

The presentation retains all 14 template slides, including Sprout Asset
Management (Asia) Limited, CE number `BDW665`, regulated activity Types 1, 4, 6
and 9, and `inquiry@www.sprout-asset.com`. It adds six editable native tables and
two editable native charts with complete workbook snapshots. Package integrity,
slide count, slide dimensions, declared fonts, native table ownership, chart
titles, quantitative chart data and first-party re-import all passed. Every
slide was rendered and inspected. The crisis maximum-drawdown field remains
explicitly `Unavailable` because the frozen snapshot did not contain a
validated result; no estimate was invented.

Both documents remain `INTERNAL DEVELOPMENT DRAFT — NOT FOR CLIENT
DISTRIBUTION`. Publication is still blocked by the provisional Yahoo Finance
benchmark and sector sources and by the outstanding formal approval of the
complete regulatory disclosure. Production was not changed.

## Date-specific crisis replay and historical reporting batch

On 23 September 2026 the user approved sending the 81 historical U16088571
ticker symbols to Yahoo Finance for provisional sector classification and
crisis-period adjusted-price retrieval. The deployed release uses crisis replay
calculation version `as-of-holdings-crisis-replay-v2` and reporting snapshot
version `month-end-v2`.

Migration execution `ibkrv2stg-bootstrap-9d15twh` applied migration 011. The
first batch attempt failed closed because Yahoo search did not return sector
fields for historical symbols OMI and VRNT. VRNT was resolved through Yahoo's
company profile. OMI is tied to IBKR conid `10814` and uses a narrow historical
Healthcare / Medical Distribution override backed by the issuer's 2024 SEC
Form 10-K. The second attempt exposed an ambiguous PostgreSQL null parameter;
an explicit portable text cast fixed it and received regression coverage.

ACR build `ck14` succeeded with immutable digest
`sha256:24181f599e57ffb5c9a5bf8b990e9a77dd14018b1f7558b6686557eafc742ea0`.
The expanded suite passed 132 tests and 26 subtests. Historical execution
`ibkrv2stg-historical-reports-lh6twsm` then succeeded:

- 81 historical holdings classified with zero unresolved records;
- 418 exact-date fixed-unit crisis replays generated;
- 20 immutable month-end snapshots frozen and uploaded;
- 20 factsheet and 20 presentation runs queued exactly once; and
- all 20 snapshots remained publication-blocked by the restricted Yahoo
  benchmark and sector sources.

The private container contains 21 U16088571 snapshot JSON blobs: the 20 new
version-2 snapshots and the earlier superseded August-development snapshot. All
21 have `application/json; charset=utf-8` content type. The latest new snapshot
is `751c2b5a-c9cd-54fb-808e-ff98edbd8d67` for 31 August 2026.

The updated daily benchmark job execution
`ibkrv2stg-benchmark-daily-ea2usk5` produced the current 18 September 2026
replay: 44 components, 31 direct histories, 13 proxies, 121 crisis observations
and maximum drawdown `-0.5388477967254892`. Portal revision
`ibkrv2stg-web--0000029` is healthy with 100% traffic and Entra redirect
enforcement intact. An authenticated browser check confirmed:

- 18 September 2026 shows crisis maximum drawdown `-53.88%` using holdings of
  that exact date; and
- 15 August 2025 shows crisis maximum drawdown `-44.62%` using holdings of that
  exact date.

After the successful run, the manual historical job's Yahoo transmission gate
was reset to `false`. It remains single-replica, zero-retry and pinned to the
immutable image. Production was not changed.

## First version-2 month-end artifact cycle

On 23 September 2026 the 31 August 2026 report pair was regenerated from
immutable snapshot `751c2b5a-c9cd-54fb-808e-ff98edbd8d67`. The factsheet uses
the current two-page Canva PDF template. The presentation uses the approved
14-slide PowerPoint template and now displays the date-specific crisis maximum
drawdown of `-51.54694873880313%`, based on holdings dated 31 August 2026,
30 direct histories and 13 documented proxies.

Both factsheet pages and all 14 presentation slides were rendered and visually
inspected. The presentation passed package integrity, font, layout, native
table, editable chart, embedded workbook and first-party re-import checks. The
first upload attempt failed closed because Azure Blob Storage rejected a
hyphenated metadata key. Retry was disabled. The metadata key was corrected to
`publication_status`, a new immutable image was built, and execution
`ibkrv2stg-report-upload-202608-5nxecff` succeeded.

| Artifact | Size | SHA-256 | Queue run |
| --- | ---: | --- | ---: |
| U16088571 factsheet PDF | 127,222 bytes | `3efaa10d0582440ad6e822e916344d092cd35ca710c24f4de2effa1246bb0feb` | 39 |
| U16088571 presentation PPTX | 888,288 bytes | `f92bf85cea37941500b1f23e9656014bbc93a2cf34ae7846561d70e217b2d9f7` | 40 |

The artifacts are stored privately under
`report-drafts/generated/U16088571/2026-08-31/`. Both queue records are
`awaiting_approval`, and both blobs carry `publication_status=blocked` because
the frozen snapshot has `publication_allowed=false`. No file was copied to the
approved-report container. The exact temporary upload job and the two ACR image
manifests that contained artifact copies were deleted after verification.
Production was not changed.

## Slide 3 KPI readability revision

On 23 September 2026 the 31 August 2026 presentation was regenerated as
revision v3 to improve the readability of slide 3. The labels above all eight
bold KPI values used 15 CSS pixels (approximately 11.25 points), and the
explanatory notes below them used 13 CSS pixels (approximately 9.75 points).
The user found this revision too small, so revision v3 was superseded by
revision v4 below.

Presentation run 40 remains `awaiting_approval` and now references
`report-drafts/generated/U16088571/2026-08-31/U16088571_Monthly_Presentation_2026-08-31_v3.pptx`.
The file is 888,307 bytes with SHA-256
`607d4262ebfd51d076444550e024508c78ed4919f397b258bb2a91ce0c7e08a5`.
Blob metadata retains `publication_status=blocked`, and no file was copied to
the approved-report container. Execution `ibkrv2stg-report-upload-v3-i49e6bs`
succeeded. The one-time upload job and its private ACR image were deleted after
verification. Production was not changed.

## Slide 3 KPI readability revision v4

Revision v4 increases all eight slide-3 KPI labels to 20 CSS pixels (15 points)
and their explanatory notes to 18 CSS pixels (13.5 points). The label and note
text boxes were also enlarged so the renderer does not shrink the text. The
bold values, financial data and other 13 slides were not changed. All 14 slides
were rendered and inspected, and slide 3 has no wrapping, clipping or overlap.
Package integrity, chart, table, font and first-party re-import checks passed.

Presentation run 40 remains `awaiting_approval` and now references
`report-drafts/generated/U16088571/2026-08-31/U16088571_Monthly_Presentation_2026-08-31_v4.pptx`.
The file is 888,317 bytes with SHA-256
`d1f39d579307964f1777a43bd4f705dd73c4f0523a331c094a12e7413dbf9b8e`.
Blob metadata retains `publication_status=blocked`. Execution
`ibkrv2stg-report-upload-v4-qti1t7i` succeeded, after which the one-time upload
job and its private ACR image were deleted. Production was not changed.

## Page-number removal revision v10

Revision v10 removes the template's bottom-right page-number shapes from slides
3 through 14. Slides 1 and 2 and every other slide object remain unchanged. All
14 slides were rendered and visually inspected. A structural search of the
exported deck confirmed that no bottom-right numeric page labels remain on the
requested slides. Package, font, table, chart and first-party re-import checks
passed.

Presentation run 40 remains `awaiting_approval` and now references
`report-drafts/generated/U16088571/2026-08-31/U16088571_Monthly_Presentation_2026-08-31_v10.pptx`.
The file is 886,712 bytes with SHA-256
`22cb4b06c98a3d738f8dcf954748cdfd63dcc2f094da08bda048412db15e2c5a`.
Blob metadata retains `publication_status=blocked`. Execution
`ibkrv2stg-report-upload-v10-f2xwcm7` succeeded, after which the one-time upload
job and its private ACR image were deleted. Production was not changed.

## Reference-deck analytical expansion revision v13

Revision v13 uses the supplied Eric TONG presentation only as a structural
reference. No reference-client name, holding or financial figure was copied.
The generated U16088571 deck expands from 14 to 17 slides by duplicating the
approved blank analytical-page design and adding a deterministic executive
summary, an exposure-and-concentration page, and a conclusions/watchlist page.
All figures come from frozen snapshot
`751c2b5a-c9cd-54fb-808e-ff98edbd8d67`. Security-level performance attribution
and trade-analysis pages were intentionally deferred because the frozen
snapshot does not yet contain a reconciled attribution feed.

All 17 slides were rendered and visually inspected. Package, layout, font,
native-table, native-chart and first-party re-import checks passed. A structural
search confirmed that the deck contains no Eric TONG identifiers, reference
account numbers or internal-development warnings. Presentation run 40 remains
`awaiting_approval` and now references
`report-drafts/generated/U16088571/2026-08-31/U16088571_Monthly_Presentation_2026-08-31_v13.pptx`.
The file is 907,222 bytes with SHA-256
`d51c52524d700178affffd2b3b14b75570508bae89ecb6b7934164efde2a62e9`.
Blob metadata retains `publication_status=blocked`. Execution
`ibkrv2stg-upload-v13-0923-mc958gl` succeeded, after which the one-time upload
job and temporary private ACR image were deleted. Production was not changed.

## Slide 3 KPI readability revision v14

Revision v14 enlarges the four executive-summary KPI labels to 20 CSS pixels
(15 points) and the explanatory notes below the bold values to 18 CSS pixels
(13.5 points). The labels were shortened where necessary so every line remains
fully readable without wrapping. The four bold values and every other slide
remain unchanged. All 17 slides were rendered and visually inspected, and the
revised slide has no clipping, overlap or unintended line breaks. Package,
layout, font, table, chart and first-party re-import checks passed.

Presentation run 40 remains `awaiting_approval` and now references
`report-drafts/generated/U16088571/2026-08-31/U16088571_Monthly_Presentation_2026-08-31_v14.pptx`.
The file is 907,200 bytes with SHA-256
`04e4350063f3ebed4fe0108f66df5565a975421ff7081cd6b9e6f6bf48d69e50`.
Blob metadata retains `publication_status=blocked`. Execution
`ibkrv2stg-upload-v14-0923-qygrmuv` succeeded, after which the one-time upload
job and temporary private ACR image were deleted. Production was not changed.

## Slide 9 KPI readability revision v5

Revision v5 applies the readable KPI typography from slide 3 to the four
drawdown cards on slide 9. Their labels use 20 CSS pixels (15 points) and their
explanatory notes use 18 CSS pixels (13.5 points), with expanded text boxes to
prevent automatic shrinking. The bold values, crisis replay explanation,
input table and all other slides remain unchanged. Every slide was rendered and
inspected; the revised slide has no wrapping, clipping or overlap. Package,
font, table, chart and first-party re-import checks passed.

Presentation run 40 remains `awaiting_approval` and now references
`report-drafts/generated/U16088571/2026-08-31/U16088571_Monthly_Presentation_2026-08-31_v5.pptx`.
The file is 888,331 bytes with SHA-256
`1feec760bec3a2fa6f5273dc72a2c9dc16105c71af415e34e85e95985d3c9288`.
Blob metadata retains `publication_status=blocked`. Execution
`ibkrv2stg-report-upload-v5-361oy6z` succeeded, after which the one-time upload
job and its private ACR image were deleted. Production was not changed.

## August internal UAT acceptance and production-readiness drills

On 23 September 2026 the user accepted the August 2026 factsheet and the latest
17-slide presentation for internal user-acceptance testing. Before recording the
acceptance, Azure Blob Storage and PostgreSQL were independently reconciled:

- factsheet run 39 remains `awaiting_approval` and references the 127,222-byte
  PDF with SHA-256
  `3efaa10d0582440ad6e822e916344d092cd35ca710c24f4de2effa1246bb0feb`;
- presentation run 40 remains `awaiting_approval` and references revision v14,
  907,200 bytes, with SHA-256
  `04e4350063f3ebed4fe0108f66df5565a975421ff7081cd6b9e6f6bf48d69e50`;
- both artifacts remain in the private `report-drafts` container with
  `publication_status=blocked`; and
- frozen snapshot `751c2b5a-c9cd-54fb-808e-ff98edbd8d67` remains `warn`,
  `publication_allowed=false`, with the restricted Yahoo benchmark and sector
  sources recorded as blockers.

Audit event 1 records the outcome as `accepted_internal_only`. No artifact was
copied to `approved-reports`, no report run was marked `published`, and the user
acceptance did not override the regulatory or licensed-data gates.

A real PostgreSQL point-in-time recovery test restored the private staging
server to `2026-09-23T13:46:21Z`. A zero-retry verifier compared the source and
restored databases and returned `PASS` for:

- 44 base tables and 41 non-volatile stable tables;
- 119,523 stable rows;
- all 11 migration versions and SHA-256 checksums;
- 41 forced row-level-security tables;
- the frozen August snapshot, publication block and data digest; and
- report runs 39 and 40 and both artifact hashes.

The restored copy correctly lacked audit event 1 because the selected recovery
point preceded the acceptance event. The temporary restored server and all
three temporary verification jobs were deleted after the successful check.

The live rollback drill temporarily activated portal revision 28 and routed
100% of staging traffic to it. The public endpoint remained HTTPS-only and
returned the expected HTTP 302 redirect to the correct single-tenant Microsoft
Entra authorization endpoint. Traffic was then restored to healthy revision 29,
revision mode was returned to `Single`, and revision 28 was deactivated with 0%
traffic.

The security regression suite now passes 134 tests. Added prompt-injection tests
confirm that malicious instructions in either a user question or a holding
description remain inside untrusted JSON input, cannot add model tools, cannot
enable response storage, and cannot introduce unapproved response fields. A
targeted repository scan found no embedded private keys, storage SAS strings,
bearer tokens or OpenAI-style API keys. `pip-audit` found no known published
vulnerabilities in the installed staging dependency set; the private
`ibkr-portfolio-etl` project package was the sole expected package not present
on PyPI. The temporary audit tooling was removed afterward. The 14-day staging burn-in begins from
this readiness baseline; employee access remains disabled.

The burn-in baseline also reconfirmed that production was not changed: the
production `ibkr-v11-daily` job retains cron `0 8 * * *` (16:00 HKT), retry
limit zero and its immutable image digest. Its 23 September execution started
at 08:00 UTC and completed successfully at 08:02:30 UTC. The staging Treasury
risk-free job completed successfully at 23:00 UTC (07:00 HKT), and the staging
benchmark job completed successfully at 00:00 UTC (08:00 HKT). The earliest
date on which the 14-consecutive-day burn-in gate can close is 7 October 2026.

## Private internal-production release

On 23 September 2026 the user clarified that the completed platform and August
reports are for authorized internal and shareholder presentation only. No
client will receive the yfinance-backed output, and Bloomberg remains the
planned licensed-data upgrade before client use.

Final release `internalprod20260923r3` was built in private ACR build `ck1f`
with immutable digest
`sha256:4e14e5f4aa57b1e7e5f0b911a9b812774776c1ca0f430e08f41177080fa92294`.
The complete suite passed 138 tests before deployment. A post-deployment
regression test for Azure's property/metadata update ordering raised the source
baseline to 139 tests; all 139 pass.

Migration `012_internal_production_release.sql` adds separate release records
for `internal_shareholder` and `client` scopes. Its database trigger rejects a
client approval unless the frozen snapshot has `publication_allowed=true`.
The internal policy permits a release only when:

- the snapshot is frozen and does not have failed quality;
- the user's acceptance has been recorded;
- exactly one factsheet and one presentation reference the accepted snapshot;
- both artifact hashes are present and verify; and
- the only remaining blockers are the three yfinance licensing blockers.

Internal release execution `ibkrv2stg-internal-release-1jta5ri` created release
decision 1. Final idempotent verification execution
`ibkrv2stg-internal-release-p4lj1c7` independently downloaded and hash-verified
both draft artifacts, then confirmed them in the private `internal-reports`
container:

| Artifact | Internal-release SHA-256 | Run status |
| --- | --- | --- |
| `U16088571_Factsheet_2026-08-31_v2.pdf` | `3efaa10d0582440ad6e822e916344d092cd35ca710c24f4de2effa1246bb0feb` | `released_internal` |
| `U16088571_Monthly_Presentation_2026-08-31_v14.pptx` | `04e4350063f3ebed4fe0108f66df5565a975421ff7081cd6b9e6f6bf48d69e50` | `released_internal` |

Each blob records `release_scope=internal_shareholder`,
`market_data_provider=yfinance`, `client_distribution=prohibited` and the
frozen snapshot ID. The container has no public access. The signed-in owner was
granted `Storage Blob Data Reader` only at this container's scope.

The final verification also confirmed `application/pdf` for the factsheet and
`application/vnd.openxmlformats-officedocument.presentationml.presentation`
for the deck. Release r3 corrects the order of Azure property and metadata
updates so idempotent reruns preserve both the MIME type and all release
metadata.

Portal revision `ibkrv2stg-web--0000031` is healthy on the immutable release
digest and receives 100% of traffic. HTTPS-only ingress and the existing
single-tenant Entra authentication challenge remain enforced. Portal and AI wording now describe
the product as private internal production and shareholder-ready. They continue
to state that client distribution is prohibited until Bloomberg is installed.

The two preliminary bootstrap executions failed closed before reaching the
database because Azure execution-level image overrides omitted the inherited
environment. Retry remained zero. The supported job-definition deployment path
preserved the Key Vault references, and execution `ibkrv2stg-bootstrap-9oghran`
then applied migration 012 successfully. No production IBKR schedule, Flex
credential, portfolio calculation or existing report hash was changed.

## Automated account-only portal synchronization

On 23 September 2026 the next production-readiness step closed the gap between
the existing daily IBKR export and the internal website. A new scheduled job,
`ibkrv2stg-account-sync-daily`, runs at `10 8 * * *` UTC (16:10 HKT), after
`ibkr-v11-daily` starts at 16:00 HKT. It is single-replica, has retry limit zero
and is pinned to release `internalprod20260923r6`, ACR build `ck1j`, digest
`sha256:ca284af372f9d16b6b7566ccacab293654727f637d5be6cbceebf942dc69174f`.

The ingestion managed identity received `Storage Blob Data Reader` at the
production `analytics-reports` container scope only. The job has no Flex secret,
raw-statement role, production database credential or access to other production
operations. Each run:

1. verifies manifest format, tenant, current HKT date, row counts and SHA-256;
2. downloads the already-generated analyst SQLite export;
3. filters it to U16088571 and data from 20 January 2025;
4. validates that no other account is present;
5. stores the filtered artifact in private versioned `input-snapshots`; and
6. idempotently upserts and reconciles the account-scoped tables.

Execution `ibkrv2stg-account-sync-daily-p7g62lc` imported deterministic filtered
hash `b943b9f040a1e54f3130956675547a163e6a69483dd5c3eb9ddeb587cb5308a4`
as run 15. It reconciled 437 NAV rows, 17,876 position rows, 435 cash snapshots,
643 cash transactions, 20,046 FX rows, 205 trades, three corporate actions and
87 referenced instruments. NAV, cash and position coverage ended on 22 September
2026. Execution `ibkrv2stg-account-sync-daily-qatxvvk` rebuilt the identical
byte hash and returned `already_imported` for run 15.

The yfinance benchmark and date-specific crisis job now follows at `30 8 * * *`
UTC (16:30 HKT), is zero-retry and is pinned to release r5 digest
`sha256:88f81b8332d9014847e6c07377e5f66c1e163f193847ef7e37710b197438832e`.
Final execution `ibkrv2stg-benchmark-daily-cs0y2rz` succeeded with 840 benchmark
observations and a 22 September holdings crisis replay: 44 components, 31 direct
histories, 13 proxies and maximum drawdown `-0.5396260873546608`.

The independent U.S. Treasury risk-free-rate job remains scheduled at
`0 23 * * *` UTC (07:00 HKT), with parallelism one and retry limit zero. It is
now pinned to release r6 digest
`sha256:ca284af372f9d16b6b7566ccacab293654727f637d5be6cbceebf942dc69174f`.
Live execution `ibkrv2stg-risk-free-daily-ovccxub` succeeded with 419 loaded
observations, latest observation date 22 September 2026, annual rate `0.0416`
and quality status `pass`.

Portal revision `ibkrv2stg-web--0000032` is healthy with 100% traffic and HTTPS
only. An authenticated browser check as `anthony.ying@wealthryse.ai` displayed
22 September 2026 as the selected/latest date, NAV USD 127,380.79, 437 NAV
observations, 44 positions, current benchmark/risk data and system-health status
`pass`. The source baseline passes 144 tests. No production schedule, Flex token,
raw IBKR file, client-publication gate or employee-access allow-list changed.

## Authenticated monthly report centre

On 23 September 2026 an authenticated report centre was added to the private
portal. It lists the 20 frozen U16088571 reporting months from January 2025
through August 2026. The list deliberately marks the 19 queued historical
months as `Generation pending`; only the completed, accepted and internally
released 31 August 2026 package is marked `Ready`.

The website's managed identity was granted `Storage Blob Data Reader` at the
exact private `internal-reports` container scope. The API does not expose a
storage key or SAS URL. Before a download it verifies the Entra user, pilot
account, frozen month, internal release state, registered report type, exact
container and blob path, MIME type, 25 MB ceiling and stored SHA-256 hash.

Release `internalprod20260923r8` was built by ACR run `ck1m` and deployed by
immutable digest
`sha256:4717fbe1ae8ff237352836137974095ae60a434909bf98677a042d3b3bf4d4b0`.
Portal revision `ibkrv2stg-web--0000034` became healthy and received 100% of
traffic. A signed-in browser session as `anthony.ying@wealthryse.ai` generated
the August links and downloaded both artifacts. Azure recorded HTTP 200 for the
prepare request and both file responses. The downloaded hashes independently
matched the approved release records:

| Artifact | Downloaded SHA-256 |
| --- | --- |
| `U16088571_Factsheet_2026-08-31_v2.pdf` | `3efaa10d0582440ad6e822e916344d092cd35ca710c24f4de2effa1246bb0feb` |
| `U16088571_Monthly_Presentation_2026-08-31_v14.pptx` | `04e4350063f3ebed4fe0108f66df5565a975421ff7081cd6b9e6f6bf48d69e50` |

The corrected source baseline passes 147 tests and 42 subtests. Production IBKR
ingestion, the daily account sync, market-data jobs, client-publication block and
single-user allow-list were not changed.

## Monthly report normalized-NAV axis standardization

On 24 September 2026 the owner requested the same clean-axis policy used by the
portal for every historical and future factsheet and 17-slide presentation.
The deterministic PDF renderer and Artifact Tool PowerPoint renderer now round
normalized performance bounds outward and use a fixed major unit of 10. A flat
series is expanded safely around its value. The PowerPoint package validator
also rejects missing, non-round or non-10-point value-axis settings.

All 20 reporting months from January 2025 through August 2026 were regenerated
from their frozen snapshots. Visual QA covered all 340 slides and all 20
factsheet chart pages. All 199 automated tests and the Azure Bicep validators
passed. The immutable Azure image was built by ACR run `ck2y` with digest
`sha256:ac778c661688a17cd4279cee608e2ed89add0f1a610624432d89f60cfa95fe96`.

Historical upgrade execution `ibkrv2stg-nav-axis-upgrade-4xu0i8n` downloaded and
SHA-256 verified all 40 private review blobs before updating their database
records. January 2025 through July 2026 remain owner-review packages. The August
factsheet and presentation retained `released_internal` status and now have
SHA-256 values `6ecf4db5d09de1cc897129af9da5092911d10728946b61263e61eb4fdbb809ff`
and `b528a2f41fc1920e30be370f1e1a277f6d7ba87db601641a94dc434642fbab0b`.
Independent downloads reproduced both hashes. The one-time upgrade job and the
temporary user Blob Contributor assignment were removed.

The permanent `ibkrv2stg-month-end-factsheet` job remains scheduled at
`0 9 1-7 * *` UTC (17:00 HKT), has retry limit zero and is pinned to the new
digest. Smoke execution `ibkrv2stg-month-end-factsheet-vdt5hvs` succeeded and
returned `already_released` for August. The production IBKR refresh remains
unchanged at `0 8 * * *` UTC (16:00 HKT), with retry limit zero.

## August factsheet classification-table revision

On 24 September 2026 the owner requested that the approved August factsheet
replace the Top 5 Products columns Symbol, CCY and Market Value with Region,
Sector and Sub-Sector. The Sector Allocation table now uses Region, Sector,
Sub-Sector and % Gross. Region is defined as the deterministic listing region;
sector and sub-sector use the validated Yahoo sector and industry fields. The
renderer fails closed if a Top 5 holding or allocation row lacks these fields.

All 43 August holdings were classified, producing 100% coverage and 23 detailed
region/sector/sub-sector groups. The displayed allocation retains the five
largest groups and combines the remainder into an explicit Other row. Both PDF
pages were rendered at 180 DPI and visually inspected. Historical NAV values,
the normalized-NAV chart, disclosures and the 17-slide presentation were not
changed. All 199 automated tests and four Azure Bicep builds passed.

ACR run `ck30` created immutable digest
`sha256:705d3dce88a0bce7120969cc7eb0d4187f7a45933c74ae929a43aef05aa707aa`.
The permanent month-end job now uses factsheet layout
`approved-august-2026-v5`, remains scheduled at 17:00 HKT on days 1-7 with
retry limit zero, and smoke execution
`ibkrv2stg-month-end-factsheet-7v6eh6f` succeeded idempotently.

Owner-approved, zero-retry execution `ibkrv2stg-fs-layout-v5-6f5ajpu`
verified and replaced the internally released August factsheet. The new PDF
SHA-256 is
`5fbf5e6f25d873068c8d71bfe63fef28b8fcf3f16ddbea0f2337ba26fc7a2d43`.
The August presentation retained its existing
`b528a2f41fc1920e30be370f1e1a277f6d7ba87db601641a94dc434642fbab0b`
hash. An independent Azure download reproduced the PDF hash and confirmed two
pages, both new headers and no old Symbol header. The one-time job was deleted;
the user's report-container role returned to Storage Blob Data Reader only.

## August factsheet reference-layout revision

On 24 September 2026 the owner supplied a completed August reference factsheet
and requested that the approved U16088571 August factsheet use its layout and
format. Layout `approved-august-2026-v6` now reproduces the reference's table
column proportions, 13-row report taxonomy, title-cased company names,
centered serif dates, lower historical-performance table, multiline headings,
and Matplotlib-style normalized-NAV chart geometry. The report continues to
use the frozen platform snapshot for all financial values; figures embedded in
the reference document were not copied into the approved database report.

Both pages were rendered at 180 DPI and visually compared with the supplied
reference. All 199 tests passed. ACR build `ck32` created immutable digest
`sha256:bff82a57856f36a916926785f181f62afcdb669961c810da5c860563d4cfdf26`.
Zero-retry execution `ibkrv2stg-factsheet-v6-upgrade-ixn1jdp` replaced only the
released August factsheet; its SHA-256 is
`08953e45a16eb36c7b519953c3e67352aed6ffdea0278228cea48a35a67090cc`
and its size is 148,869 bytes. An independent Azure download reproduced both.
The released presentation was independently re-downloaded and remained
unchanged at SHA-256
`b528a2f41fc1920e30be370f1e1a277f6d7ba87db601641a94dc434642fbab0b`.

The permanent `ibkrv2stg-month-end-factsheet` job is pinned to the v6 digest,
remains zero-retry, and retains schedule `0 9 1-7 * *` UTC (17:00 HKT on days
1-7). Smoke execution `ibkrv2stg-month-end-factsheet-7bfuudn` returned
`already_released`. The one-time upgrade job and its temporary
container-scoped Blob Data Contributor assignment were removed after
verification.

## Historical and future factsheet v6 lock

On 24 September 2026 the owner approved
`U16088571_Factsheet_2026-08-31_Approved_v6.pdf` as the authoritative layout
for every past and future U16088571 factsheet. A two-phase, fail-closed
migration rendered the 19 frozen month ends from January 2025 through July
2026 into a private QA prefix before changing any portal-visible file.

QA execution `ibkrv2stg-factsheet-v6-hist-u07xfh4` produced 19 two-page PDFs
and a signed-by-hash manifest. Every byte count and SHA-256 matched after an
independent download. All 38 pages were rendered at 144 DPI and visually
inspected; full-resolution checks covered the shortest, middle and longest
historical tables and the chart-date spacing. Publish execution
`ibkrv2stg-factsheet-v6-hist-mpab8sy` then replaced only the existing factsheet
review blobs and factsheet generation-run metadata. The presentation renderer,
presentation blobs and presentation database records were not modified.
Post-publish downloads reproduced all 19 QA hashes exactly.

ACR build `ck34` created immutable digest
`sha256:56d63def7ef484dd9b470c404d5af3d9711711b797701946e4fd2df1db7155b0`.
The permanent `ibkrv2stg-month-end-factsheet` job is pinned to this digest,
reports layout `approved-august-2026-v6`, has retry limit zero and retains cron
`0 9 1-7 * *` UTC. Smoke execution
`ibkrv2stg-month-end-factsheet-5uwgcs7` succeeded idempotently with
`already_released` for August. All 199 automated tests pass. The temporary
migration job and its redundant container-scoped role were removed after the
publisher identity's permanent storage-account role was independently
confirmed.
