# Phase 3 market data

The silver tables the frontier analytics' phase 3 sections read. Each feed has its own section
below: what it captures, its licence, its held migration and its day, how to switch it on, and how
to undo it. The gold sections read these tables through `src/ibkr_etl/market_inputs.py`, which is
not part of this change.

This file covers daily volumes and factor returns. Macro series and 13F fund holdings, built the
same way, are in `docs/PHASE3_DATA_MACRO_13F.md`.

Everything here is **off**. Each table comes from a held migration that no bootstrap or job
applies. Each job does nothing unless its setting is `on`, and its Bicep deploys with the setting
`off`. Nothing changes live until the switch-on steps below. The columns are the fixed phase 3
schemas; none were added.

What every feed here has in common:

- **The table.** Its draft creates it in `public`, then moves it into `silver` and leaves a
  `security_invoker` view of the same name in `public`, as 070 did for `market_data_prices`.
  `tenant_id` and the `tenant_isolation` policy apply, with forced row-level security. Readers use
  the unqualified name and treat a missing relation as "not loaded yet".
- **Grants.** The draft grants nothing. `bootstrap_postgres.py`, which runs the migration, gives:
  - the job's app login SELECT, INSERT, UPDATE and DELETE;
  - the website login SELECT;
  - the approval login SELECT on the date column only (`layer_quality.COUNT_COLUMNS`).

  The approval login reads `market_data_prices` whole, for the reference-market approvals. These
  tables do not need that.
- **Registered, held.** Each table is registered in:
  - `catalog.json`, with `"held"`;
  - a contract under `infra/medallion/contracts/silver/`;
  - `layer_quality.VOLUMES` (`held=True`);
  - `layer_freshness.RULES` (held, and not required, so an empty table never raises an incident).

  The checks wait for the table, so the administrator's reports read as today until the migration
  runs. The publish gate covers gold only and is unchanged.
- **The job.**
  - Each job is its own Container Apps job, on a schedule outside 07:50 to 09:25 UTC. It never
    runs inside the four jobs the publish gate binds.
  - It uses the platform image and the ingestion identity, which already holds Storage Blob Data
    Contributor on `bronze-market-data`. No new role assignment.
  - Writes are one transaction per instrument or region. Each run is recorded in lineage under its
    catalog job name, with the bronze files it read or stored as inputs and the table as output.
  - Exit codes: 0 for success, 1 for a failure, and 65 for **held**. Held means the table does not
    exist yet, and the job stopped before reading, fetching or writing anything.

## Daily volumes

| | |
|---|---|
| Table | `silver.daily_volumes` (view `public.daily_volumes`): one row per tenant, instrument and exchange trading date |
| Held migration and its undo | `infra/medallion/drafts/daily_volumes.sql`, `daily_volumes_undo.sql` |
| Job | `ibkrv2stg-daily-volumes`, `infra/staging-daily-volumes-job.bicep`, daily 06:30 UTC, 15 minutes at most |
| Code | `scripts/load_daily_volumes.py`, `src/ibkr_etl/daily_volumes.py` |
| Settings | `DAILY_VOLUMES_LOAD`, and `DAILY_VOLUMES_FETCH` for the holdings' own fetch (each `off` unless set to `on`) |
| Bronze | `bronze-market-data`, `yahoo-chart/`: read; with the fetch on, the holdings' responses are written there first |
| Licence | `yfinance-internal-only` |
| Freshness | `silver.daily_volumes`: due 07:00 UTC, 4 days old at most; held and not required |

### What it captures

Two fetches leave Yahoo chart responses in bronze. `ibkrv2stg-derived-daily` asks Yahoo's chart
API for the instruments in `market_data_prices`, through `market_data.fetch_daily_closes`. Today
those are SPY and the Hang Seng index `^HSI`, from 13 Jan 2025 to today. This job's own fetch asks
for the holdings (below). Each response carries every day's volume beside its close. When raw
capture is on, the response is kept as received under
`yahoo-chart/<yyyy>/<mm>/<dd>/<symbol>_<start>_<end>_1d-<sha>.json`, or under `yahoo-chart/history/`
once its window has settled. This job reads those files:

- **Instruments.** For the benchmarks, the job takes the chart rows of `market_data_prices` (source
  `Yahoo Finance chart API`), each with the symbol and currency of its newest row. The holdings come
  from the fetch below. A capture belongs to an instrument when its request key starts with the
  instrument's symbol, and the response's own symbol and currency match.
- **Dates.** They are the exchange's own, read the way `fetch_daily_closes` reads the closes, so a
  volume and its close share a date.
- **Missing and zero volumes.** A day without a usable volume (null, not finite, or below zero) is
  skipped and never stored as NULL. A zero is kept as Yahoo reports it, for example on an exchange
  holiday. Readers skip zeros.
- **A bar still trading.** If a capture was requested during that day's regular session, the day is
  left out. The next capture has the full day.
- **Several captures of one day.** The newest capture wins, in the run and in the table. A row is
  only replaced by a capture requested later that has a different volume, so loading the same or
  an older capture again changes nothing.
- **Which captures a run reads.**
  - The daily run reads the last three dated folders (`--days N`).
  - A backfill, `--from YYYY-MM-DD [--to YYYY-MM-DD]`, reads every capture kept, `history/`
    included, and keeps the days in that range.
  - Each capture holds the whole window from 13 Jan 2025, so one capture can backfill everything
    since then.
  - `--instrument ID` narrows a run.
- **Output.** The job prints one JSON line per instrument: captures, rows, written, unchanged,
  skipped days, first and latest date, and unreadable captures. A summary follows, with one of
  three statuses:
  - `success`;
  - `no_captures`: none were found. This is not a failure, but it says the chart fetch is not
    keeping its responses;
  - `failed` (exit 1): an instrument could not be written, or a capture could not be read.

**Before switching on: the benchmarks' captures.** Today `ibkrv2stg-derived-daily` does not set
`MARKET_DATA_RAW_CONTAINER`, so its chart responses are not kept, and this job finds none for SPY
and `^HSI` (`no_captures`, unless the holdings' fetch kept something). To keep them, add raw capture
to that job (`infra/staging-derived-refresh.bicep`): `MARKET_DATA_RAW_CONTAINER=bronze-market-data`,
with its identity able to write there. That job is one of the four the publish gate binds, so this
is its own decision and its own day, and it is not part of this change. The holdings do not need
it: their fetch keeps its own responses.

### The holdings' own fetch

The Liquidity section and the pre-trade market impact need the holdings' volumes. The benchmarks'
fetch above is not changed, so this job fetches the holdings itself. The job always passes
`--fetch`. It fetches nothing unless `DAILY_VOLUMES_FETCH=on` (with `DAILY_VOLUMES_LOAD=on`); then,
after the held check and before the benchmarks' load:

- **Which holdings.** Every instrument held (quantity not zero) by a physical account on that
  account's latest position date. They come from silver `subject_position_series` (`source_kind`
  `physical_account`), with `instruments` and `instrument_classifications`. Derived subjects add
  nothing of their own. No bronze table is read.
- **Stored under the conid.** A holding's rows have its IBKR conid as `instrument_id`, the key the
  gold sections join positions on. The benchmarks keep their `market_data_prices` ids.
- **The Yahoo symbol.** It is worked out as the platform already does it:
  - the candidates come from `sector_classification.candidate_symbols` (for example `0700.HK` for
    SEHK 700, `BRK-B` for BRK B, and `.T`, `.AX`, `.KS` or `.KQ`, `.TW` or `.TWO`, `.L`);
  - they are tried in order, as `crisis_replay` tries them;
  - the symbol the sector lookup matched (`instrument_classifications.lookup_symbol`, provider
    `Yahoo Finance search profile`) goes first when it is one of those candidates. An owner's override
    to another line, such as a warrant's underlying, is not used;
  - a candidate counts only when Yahoo answers with a chart for that symbol in the holding's currency.
- **Never guessed.** These are listed in the summary (`not_mapped`) and not fetched:
  - an IBKR asset category other than `STK` (options, warrants, bonds, funds);
  - a receivable (`.REC`);
  - a contract with no symbol in `instruments`, or no currency.

  A holding whose every candidate Yahoo refuses (404, or another currency) is listed as `not_found`,
  with the symbols tried.
- **The fetch.** Each request is `market_data.fetch_daily_closes`, daily interval, so `raw_capture`
  keeps the response as received under `yahoo-chart/<yyyy>/<mm>/<dd>/` first, and market_data's three
  attempts with backoff apply. The volumes are then read back from bronze and parsed exactly as the
  load above parses them. A response that was not kept is never parsed (`not_captured`, a failure).
- **The window.** The last year for a holding with no volumes stored yet. After that, the last 10
  days, or from the day before its latest stored day when that is older, so a pause leaves no gap.
- **Pacing.**
  - A fixed pause of 1 second between two requests.
  - At most 60 requests a run (`--max-symbols`) and 480 seconds of fetching, well inside the
    15-minute run.
  - One request per symbol and window, even when two conids share it.
  - What is left waits for the next run, which starts with the least recently loaded. The rest also
    wait if Yahoo still answers 429 (too many requests) after market_data's retries, or if raw
    capture switches itself off (a storage error, or its time budget used).
- **Its raw copies.** `MARKET_DATA_RAW_CONTAINER=bronze-market-data`, with
  `MARKET_DATA_RAW_BUDGET_SECONDS=120`, is set on this job only. With raw capture unset, or writing
  to another container than the one the load reads, the fetch asks Yahoo for nothing and the run
  fails (`capture_off`).
- **The identity.** The job runs as the ingestion identity. It holds Storage Blob Data Contributor on
  the `bronze-market-data` container itself (`ingestionBronzeMarketDataContributor` in
  `infra/staging.bicep`, assigned 18 Oct 2026 in medallion S4a). That role covers the reads and the
  writes, and it is not the account-wide role Medallion narrows on Thu 22 Oct, so no role assignment
  is added. Step 7 below checks it is still there.
- **Output.**
  - First, one JSON line per holding: outcome, Yahoo symbol, symbols tried, window, captures and
    the load's counts.
  - Then the benchmarks' lines.
  - Then the summary, with a `fetch` part: holdings, mapped, requests, fetched, captured, rows,
    written, `not_mapped`, `not_found`, `failed`, `deferred` with the reason, and raw capture's
    counts.
  - The status is `failed` (exit 1) when a request failed, a response was not kept, a holding could
    not be written, a capture could not be read, or raw capture is not set up. Holdings not mapped,
    not found or deferred do not fail the run.
- **Before switching on: the contract.** The silver contract
  (`infra/medallion/contracts/silver/daily_volumes.yaml`) says `instrument_id` is as in
  `market_data_prices`. Its warning rule `instrument_has_prices` counts every row whose instrument has
  no closes there, so each holding's rows would count. The contract's owners need to let that
  description and rule accept a conid in `instruments` (step 8).

**Licence.** Yahoo chart data is `yfinance-internal-only`, as for the other Yahoo feeds. That
licence is in the job's `dataLicense` tag and in the contract's `dataLicense` property. Internal use
only.

### Switching on, in order

**The day.** A free day after Wed 25 Nov, with the migration run alone. The number is the next free
migration number on that day: 078 if the quant drafts have taken 076 and 077 and nothing else has
come first. Factor returns can take the next free day after that; the two drafts are tested in
either order.

Run each line from a clone of `main` after `az login`. Run steps 3, 5, 6, 9 and 10 outside 07:50 to
09:25 UTC. Steps 7 to 10 switch on the holdings' fetch, on a later free day once the load runs.

| # | Where | PowerShell line | What it does |
|---|---|---|---|
| 1 | PR | `git mv infra/medallion/drafts/daily_volumes.sql migrations/postgres/078_daily_volumes.sql` | In the same PR, drop `"held"` from the catalog entry `public.daily_volumes` (`test_medallion_catalog` asks for this). The undo stays in drafts. Merge, then build the bootstrap image from `main`. |
| 2 | Admin VM | `python3 scripts/medallion_layers.py snapshot --account <pilot account> --out before-078.json` | The read-only snapshot (`MEDALLION_S3_RUNBOOK.md`, step 2). |
| 3 | Ying's PC | `az containerapp job start -g rg-ibkr-database-v11 -n ibkrv2stg-bootstrap --container-name postgres-bootstrap --image <bootstrap image> --args scripts/bootstrap_postgres.py --only-migration 078_daily_volumes.sql --allow-pending <held files still pending> --allow-applied-without-file 067_hong_kong_china_external_market_data_approval.sql` | Applies 078 alone. It ends `Migrations applied: 078_daily_volumes.sql.`, and a second run applies nothing. Then run the runbook's step 4 check, and record the migration in `deploy/applied-migrations.json` in a PR. |
| 4 | Ying's PC | `az deployment group create -g rg-ibkr-database-v11 -f infra/staging-daily-volumes-job.bicep -p registryName=<registry> keyVaultName=<vault> identityResourceId=<ingestion identity id> imageDigest=<platform digest>` | Deploys the job with `dailyVolumesLoad=off`. Its 06:30 run prints `{"status": "off", ...}` and exits 0. Record the job in `deploy/deployed-images.json` in a PR. |
| 5 | Ying's PC | `az containerapp job start -g rg-ibkr-database-v11 -n ibkrv2stg-daily-volumes --container-name daily-volumes --image <its image> --env-vars DAILY_VOLUMES_LOAD=on --args scripts/load_daily_volumes.py --from 2025-01-13` | One backfill by hand, needing the captures (above). Check the summary: `status` `success`, `written` above 0, and no unreadable captures. Then spot-check a few rows against Yahoo. |
| 6 | Ying's PC | step 4's line with `dailyVolumesLoad=on` added to `-p` | From the next 06:30 run, each run loads the last three days' captures. Once it has run for a week, the freshness rule can be made required. |
| 7 | Ying's PC | `az role assignment list --assignee <ingestion identity principal id> --scope <storage account id>/blobServices/default/containers/bronze-market-data --query "[].roleDefinitionName" -o tsv` | Read only. It should print `Storage Blob Data Contributor`. If it does not, stop: the job needs Storage Blob Data Contributor for the ingestion identity on the `bronze-market-data` container scope, added with Medallion's agreement, not by this change. |
| 8 | PR | the contract change above | The contract's `instrument_id` and `instrument_has_prices` accept a conid in `instruments`, with its owners' review. |
| 9 | Ying's PC | `az containerapp job start -g rg-ibkr-database-v11 -n ibkrv2stg-daily-volumes --container-name daily-volumes --image <its image> --env-vars DAILY_VOLUMES_LOAD=on DAILY_VOLUMES_FETCH=on --args scripts/load_daily_volumes.py --fetch --max-symbols 5` | One small fetch by hand. In the summary's `fetch`, check: `status` `success`, `captured` equal to `fetched`, and the `not_mapped` and `not_found` lists. Check the captures are in `yahoo-chart/<today>/`, then spot-check a few rows against Yahoo. |
| 10 | Ying's PC | step 4's line with `dailyVolumesLoad=on dailyVolumesFetch=on` added to `-p` | From the next 06:30 run, the job fetches up to 60 holdings, then loads them and the benchmarks. Until every holding has volumes, each run fetches a year for up to 60 of them. |

### Undoing

- **The fetch alone.** Redeploy with `dailyVolumesFetch=off`. The job loads only the benchmarks
  again. The holdings' rows and their captures stay.
- **Lighter.** Redeploy with `dailyVolumesLoad=off`. The rows stay, and nothing reads them until
  the gold section that uses them is switched on.
- **Whole.** `daily_volumes_undo.sql` drops the table and its view, with their policy, comments and
  grants, and nothing else. `market_data_prices` and the bronze captures are untouched.
  - Run the undo as its own numbered migration with `--only-migration`, or by psql.
  - No copy is needed first. A backfill rebuilds the benchmarks' rows from bronze. The holdings'
    rows come back from the fetch: each holding then held, with no rows, gets a year again.
  - If the migration should run again later, delete its ledger row by hand.
  - Remove the job with `az containerapp job delete -g rg-ibkr-database-v11 -n ibkrv2stg-daily-volumes`.

## Factor returns

| | |
|---|---|
| Table | `silver.factor_returns` (view `public.factor_returns`): one row per tenant, region, factor, return date and vintage |
| Held migration and its undo | `infra/medallion/drafts/factor_returns.sql`, `factor_returns_undo.sql` |
| Job | `ibkrv2stg-factor-returns`, `infra/staging-factor-returns-job.bicep`, Saturdays 06:00 UTC, 20 minutes at most |
| Code | `scripts/load_factor_returns.py`, `src/ibkr_etl/factor_returns.py` |
| Setting | `FACTOR_RETURNS_LOAD` (`off` unless set to `on`) |
| Bronze | `bronze-market-data`, `kenneth-french/`: each file as received, before it is parsed |
| Licence | `kenneth-french-terms-unchecked-internal-only` |
| Freshness | `silver.factor_returns`: due 07:00 UTC, 75 days old at most; held and not required |

### What it captures

The job works through the regions of `fama_french.REGIONS`: `us`, `developed` and
`asia_pacific_ex_japan`. These lowercase keys are what `region` holds.

1. **Download.** It downloads each region's daily five-factor file and daily momentum file (zip)
   from Kenneth French's data library (`fama_french.BASE_URL`).
2. **Store first.** It stores each response as received, through `raw_capture`, before parsing
   anything:
   - feed `kenneth-french`;
   - blob metadata `dataLicense=kenneth-french-terms-unchecked-internal-only`, set by
     `raw_capture.FEED_LICENSES`;
   - files whose data ended over a week earlier are finished history, filed once as
     `kenneth-french/history/<region>_<file>-<sha>.zip`.

   The job sets `MARKET_DATA_RAW_CONTAINER=bronze-market-data`, with a 60-second budget. With it
   empty, capture is off: the load still runs, but its rows cannot be rebuilt from bronze.
3. **Parse.** It parses with `fama_french.parse_french_csv`:
   - daily rows only: the notes, the monthly and annual sections, and the copyright line are left
     out;
   - `-99.99` (missing) is skipped;
   - percent is divided by 100;
   - only the seven factors are kept: `Mkt-RF`, `SMB`, `HML`, `RMW`, `CMA`, `RF` and `MOM` (the
     momentum column, `Mom` or `WML` in the files).
4. **Upsert.** It writes one transaction per region. One file or region failing never stops the
   next; the run then exits 1.

How the stored rows behave:

- **Vintages.** The vintage is the day the file was published (its `Last-Modified` date, in UTC),
  or the day it was fetched when the server does not say. Every vintage is kept:
  - a vintage holds the rows it added or changed against the latest earlier vintage, so a weekly
    run over an unchanged file writes nothing;
  - **readers take the latest vintage per region, factor and date**;
  - the series as published on vintage V is, per date, the latest vintage on or before V.
- **ETF stand-ins.** These are stored only with `--etf-proxy`, which the Bicep does not pass, so it
  is a later decision.
  - They come from `fama_french.load_proxy_factors`. Their fund histories are kept in bronze under
    `yahoo-crisis-history`, as the crisis windows' are.
  - They are stored with `source = 'etf-proxy'` and the run date as their vintage, and only for
    days after the region's latest library date.
  - A library row later replaces a stand-in, either under a later vintage or by overwriting the
    same vintage. A stand-in never replaces a library row.
  - Readers that want library figures only filter `source = 'kenneth-french'`.
- **The website.** `fama_french.py` is unchanged: the website's factor model still downloads the
  files on request until it is switched to read silver.

**Licence.** The library is free to download, but its terms for commercial use have not been
checked. The data is for internal use only and is not passed on. This is stated in the blob
metadata, the job's `dataLicense` tag and the contract's limitations. Before any outside use, check
the terms, then change `raw_capture.FEED_LICENSES`, the tag and the contract.

### Switching on, in order

**The day.** A free day after Wed 25 Nov, with the migration run alone. Its number is the next free
number on that day (079 if daily volumes took 078). Run steps 3, 5 and 6 outside 07:50 to 09:25 UTC.

| # | Where | PowerShell line | What it does |
|---|---|---|---|
| 1 | PR | `git mv infra/medallion/drafts/factor_returns.sql migrations/postgres/079_factor_returns.sql` | In the same PR, drop `"held"` from `public.factor_returns` in `catalog.json`. The undo stays in drafts. Merge, then build the bootstrap image. |
| 2 | Admin VM | `python3 scripts/medallion_layers.py snapshot --account <pilot account> --out before-079.json` | The read-only snapshot. |
| 3 | Ying's PC | `az containerapp job start -g rg-ibkr-database-v11 -n ibkrv2stg-bootstrap --container-name postgres-bootstrap --image <bootstrap image> --args scripts/bootstrap_postgres.py --only-migration 079_factor_returns.sql --allow-pending <held files still pending> --allow-applied-without-file 067_hong_kong_china_external_market_data_approval.sql` | Applies 079 alone. It ends `Migrations applied: 079_factor_returns.sql.` Check, then record it in `deploy/applied-migrations.json`. |
| 4 | Ying's PC | `az deployment group create -g rg-ibkr-database-v11 -f infra/staging-factor-returns-job.bicep -p registryName=<registry> keyVaultName=<vault> identityResourceId=<ingestion identity id> imageDigest=<platform digest>` | Deploys the job with `factorReturnsLoad=off`: its Saturday run prints `{"status": "off", ...}`. Record the job in `deploy/deployed-images.json`. |
| 5 | Ying's PC | `az containerapp job start -g rg-ibkr-database-v11 -n ibkrv2stg-factor-returns --container-name factor-returns --image <its image> --env-vars FACTOR_RETURNS_LOAD=on --args scripts/load_factor_returns.py --region us` | One region by hand. Check the summary: `status` `success`, and both files with a vintage and a `latest_date` about a month back. Check `raw_capture.written` is 2, and that the rows match the library's own figures for a few days. Then run it without `--region`. The first full load writes about 250,000 rows. |
| 6 | Ying's PC | step 4's line with `factorReturnsLoad=on` added to `-p` | From the next Saturday, each run fetches the six files and writes only what changed. |

### Undoing

- **Lighter.** Redeploy with `factorReturnsLoad=off`. The rows stay.
- **Whole.** `factor_returns_undo.sql` drops the table and its view, with their policy, comments
  and grants, and nothing else. The `kenneth-french` captures stay.
  - Keep a copy first (`pg_dump -t silver.factor_returns`). Earlier vintages that can no longer be
    downloaded live on only in those captures.
  - Run the undo as its own numbered migration, or by psql, and delete the ledger row by hand if
    the migration should run again later.

## Tests

- `tests/test_phase3_volumes_factors.py` needs no database and no network; every fetch is a fake.
  - Volumes:
    - synthetic chart responses: known values, nulls and bad values skipped, zero kept;
    - exchange-local dates, the same as `fetch_daily_closes` gives the closes;
    - the bar still trading left out;
    - which captures a run reads, and the newest capture winning.
  - Factors:
    - synthetic French files: percent to fraction; the notes, the annual section and the copyright
      line ignored; the momentum file; the zip and the vintage;
    - each file stored in bronze with its licence before it is parsed.
  - Both jobs:
    - doing nothing when off;
    - exiting 65 when the table is missing, having read and fetched nothing;
    - their registrations, and their job definitions (off, own job, outside the daily window, no
      role assignments).
- `tests/test_phase3_volumes_factors_db.py` needs PostgreSQL (`AGENT_TEST_PG_ADMIN_DSN`).
  - Each draft is applied alone with bootstrap, as its day will run it, then checked for grants,
    row security and constraints, and the contract quality rules are run.
  - Volumes loads: idempotent; newest capture wins; an older capture never replaces a row; backfill
    dates.
  - Factor loads: vintages kept; an unchanged file writes nothing; a revision goes under the new
    vintage.
  - Stand-ins: only with the flag and only after the library's latest day, and replaced by the
    library's rows.
  - Lineage is recorded, and both undos, in either order, leave no trace.
