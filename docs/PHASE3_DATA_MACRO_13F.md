# Phase 3 data: macro series and 13F holdings (part B)

Design: `frontier-analytics/phase3-data-design.md` (18 Oct 2026), items 3 and 4. The fixed
table schemas are shared with the gold worker. The integrator merges this file with
`docs/PHASE3_DATA.md` (part A: daily volumes and factor returns).

Everything here is **off**:

- the two tables are held migrations, which no bootstrap or job applies;
- each job does nothing unless its setting is `on`;
- the Bicep is not deployed.

Nothing changes live until the switch-on steps below. With the settings off, no existing job,
table, view, check or page behaves differently.

| What | Where |
|---|---|
| Bronze feeds `fred-series` and `sec-13f` | `src/ibkr_etl/raw_capture.py` (`FEEDS`; `http(..., licence=)`) |
| Macro loader | `src/ibkr_etl/macro_series.py`, `scripts/load_macro_series.py` |
| 13F loader | `src/ibkr_etl/sec_13f.py`, `scripts/load_institutional_holdings.py` |
| Filer list (starter, **not reviewed**) | `src/ibkr_etl/data/sec_13f_filers.json` |
| Held migrations and their undos | `infra/medallion/drafts/macro_series.sql`, `macro_series_undo.sql`, `institutional_holdings.sql`, `institutional_holdings_undo.sql` |
| Contracts | `infra/medallion/contracts/silver/macro_series.yaml`, `institutional_holdings.yaml` |
| Jobs | `infra/staging-macro-series-job.bicep`, `infra/staging-institutional-holdings-job.bicep`; catalog `jobs` entries |
| Monitoring | `layer_freshness.RULES` (held), `layer_quality.VOLUMES` (held, recorded, not judged) |
| Tests | `tests/test_phase3_macro_13f.py` (offline), `tests/test_phase3_macro_13f_db.py` (local PostgreSQL) |

## What each feed captures

Every response is stored in `bronze-market-data` **before it is parsed**, exactly as received,
through `raw_capture`. Storage is append-only, named by content hash and under legal hold. Each
blob's metadata carries:

- `feed`, `provider`, `source_url` (with no credentials), `http_status`, `sha256`;
- the job run id;
- `licence`, which names the licence the data is kept under.

A failed capture never fails the fetch.

| Feed | What one blob is | Name |
|---|---|---|
| `fred-series` (new) | One FRED keyless CSV download, `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<series>&cosd=<start>`, for one series from the start date (default 2015-01-01) to the latest observation | `fred-series/<yyyy>/<mm>/<dd>/<series>_<start>-<sha>.csv` |
| `sec-13f` (new) | One SEC EDGAR response: a filer's submissions index (`data.sec.gov/submissions/CIK##########.json`, plus older pages when the window reaches them), or a filing's `index.json`, cover page (`primary_doc.xml`) or information table | Submissions: `sec-13f/<yyyy>/<mm>/<dd>/submissions_CIK<cik>-<sha>.json`. Filing documents: `sec-13f/history/<cik>_<accession>_<document>-<sha>.<ext>`, filed once because an accession never changes |
| `yahoo-chart` (existing) | USD/CNH: the Yahoo chart response for `CNH=X`, through `market_data.fetch_daily_closes`, as for the other Yahoo series | As today |

`sec-13f` has no daily freshness feed rule (`tests/test_layer_freshness.py` names the
exception). Filings come quarterly, and their documents are filed under `history/`.
`fred-series` has one, not required, due 23:30 UTC.

## Licences (Medallion condition 5)

These were checked from each series' FRED notes, as quoted in `macro_series.SERIES`. The tests
read no network.

| Series | Source in FRED's notes | Licence state | Kept |
|---|---|---|---|
| DGS10, DGS2 | Board of Governors of the Federal Reserve System, H.15 | US government data, no copyright notice: public domain, citation requested | Yes |
| DTWEXBGS | Board of Governors of the Federal Reserve System, H.10 | As above | Yes |
| DCOILBRENTEU | U.S. Energy Information Administration, Spot Prices | US government data, no copyright notice. EIA republishes these spot prices from a commercial source: **confirm before switch-on** | Yes (confirm) |
| VIXCLS | Chicago Board Options Exchange | Its notes say "Copyright, 2016, Chicago Board Options Exchange, Inc. Reprinted with permission." **Not public domain** | **No**, until the owner confirms a licence and sets `MACRO_SERIES_LICENSE_CONFIRMED=VIXCLS` |
| ICE BofA (`BAML*`) | ICE Data Indices | Copyrighted | Never: `fred_url` refuses any id that starts with BAML |
| USDCNH (`CNH=X`) | Yahoo chart | The existing internal-only rule, `dataLicense: yfinance-internal-only` | Yes, internal only |
| 13F information tables | SEC EDGAR | Public filings | Yes |

The design listed VIX among the public-domain FRED series. Its FRED notes say otherwise, so it is
**left out by default**. Leaving it out means the macro section has no volatility exposure until
the owner decides.

HIBOR stays in `risk_free_rates`. It is not copied here, and readers read `risk_free_rates`
directly.

## The tables (fixed schemas)

Both tables follow the market-data silver pattern:

- created in `public`, then moved into `silver` with a `security_invoker` view of the same name
  left in `public`;
- `tenant_id` defaults to `app_tenant_id()`, with row security enabled and forced and the
  `tenant_isolation` policy;
- dates as `yyyy-MM-dd` text, timestamps as ISO 8601 UTC text, numbers as double precision;
- CHECK constraints on every format and allowed value.

No columns were added beyond the fixed schemas.

**`silver.macro_series`**
- Columns: `series_id`, `observation_date`, `value`, `units` (`percent`, `index`,
  `usd_per_barrel`, `cnh_per_usd`), `source` (`fred`, `yahoo-chart`), `vintage`, `fetched_at`.
- Key: (tenant_id, series_id, observation_date, vintage).
- Yields are in percent as FRED publishes them (4.25 means 4.25%).
- **Vintages.** The vintage is the UTC date of the fetch. A value is stored under a new vintage
  only when its date is new or its value differs from the latest stored vintage of that date, so
  every FRED revision is kept and an unchanged history is not stored again every day. A rerun on
  the same day rewrites the same rows.
- Readers take the latest vintage per date:
  `SELECT DISTINCT ON (series_id, observation_date) ... ORDER BY series_id, observation_date, vintage DESC`.

**`silver.institutional_holdings`**
- Columns: `filer_cik` (10 digits), `filer_name`, `period` (quarter end), `cusip` (9 characters),
  `issuer`, `shares`, `shares_type` (`SH`, `PRN`), `value_usd`, `put_call` (`''`, `PUT`,
  `CALL`), `accession`, `filed_at`, `fetched_at`.
- Key: (tenant_id, filer_cik, period, cusip, put_call, accession).
- Index on (tenant_id, cusip, period), for the crowding reader's "who holds our CUSIPs".
- Readers take each filer's latest accession for a period: the newest `filed_at`, then the
  highest `accession` (`sec_13f.LATEST_SQL`). Accession numbers start with the filing agent's
  CIK, so the highest accession alone is not the newest filing. **The gold reader must order by
  `filed_at` first.**

**Our instruments → CUSIP:** `sec_13f.cusip_from_isin(isin)`.
- For a US ISIN with a valid check digit it returns `isin[2:11]`.
- It returns None for a non-US or malformed ISIN.
- 13F covers US-listed securities only.

## The loaders

Both loaders follow the same pattern as the other jobs:

- off unless their setting is `on` (anything else, a typo included, is off);
- one transaction per series or filing, under an advisory lock, so one failure leaves the rest
  stored;
- lineage under their catalog job name (`lineage.run_main`), with the output table and row
  count, and the run id on every capture;
- they never write gold and read nothing but their own table.

| Exit | Meaning |
|---|---|
| 0 | Done, or off (`{"status": "off", ...}`, no lineage record) |
| 1 | A series, filer or filing failed; the others are stored |
| 65 | Held: the table's migration has not run. Nothing fetched or written (as `build_quant_analytics.py`'s 65) |
| 75 | Started inside the daily chain's window (07:50 to 09:25 UTC). Nothing done |
| 78 | 13F only: no usable `SEC_USER_AGENT`, or the filer list is not marked reviewed. Checked before any database or network work |

**Macro: `scripts/load_macro_series.py`** (job `ibkrv2stg-macro-series`, 22:30 UTC daily, after
the US close and FRED's daily updates).
- For each series it may keep, it downloads the history from `--since` (default 2015-01-01) and
  captures the raw file.
- It parses the CSV:
  - both FRED header styles, `DATE` or `observation_date`;
  - `.` or an empty cell is a day with no observation, which is skipped and counted;
  - a web page instead of CSV, a bad number, a non-finite value, a bad date, or two values for
    one date rejects the series.
- It checks each value against its unit's range, then stores what is new or revised.
- USD/CNH takes completed days only (to yesterday), from Yahoo through `market_data`, captured
  as `yahoo-chart`.
- Settings: `MACRO_SERIES_LOAD` (off|on) and `MACRO_SERIES_LICENSE_CONFIRMED` (comma-separated
  restricted series the owner has licensed; empty by default).

**13F: `scripts/load_institutional_holdings.py`** (job `ibkrv2stg-institutional-holdings`, 22:45
UTC on the 20th of Feb, May, Aug and Nov, after each quarter's 45-day deadline).

SEC fair access:
- every request sends `SEC_USER_AGENT`, which must name the operator and give a contact email
  address on one line (for example `Company Name contact(at)company.example`, with a real at
  sign). It is given at deploy time and is never written in code or Bicep;
- requests start at least 0.25 s apart, at most 4 a second against SEC's 10;
- 429, 5xx and network errors are retried 3 times with 2, 4 and 8 s backoff, or SEC's
  `Retry-After`, capped at 60 s;
- 404 and 403 are not retried. A 403 says to check `SEC_USER_AGENT`.

Each run reads the last 4 quarter ends (`--quarters`, 1 to 8):
- For each filer on the reviewed list it fetches the submissions index, then for each 13F-HR or
  13F-HR/A in the window the filing index, the cover page and the information table.
- Accessions already stored are skipped (`--refresh` reads them again). So **the first run is
  the backfill of the last 4 quarters**, and each quarterly run after it adds the new quarter
  and any late amendment within a year.
- No new filer or filing starts after `--minutes` (default 45) or inside the daily window. The
  job's limit is one hour, and what was left is picked up by the next run.
- `--cik` limits a run to filers on the list.
- It prints one JSON line per filing (stored, skipped with its reason, or failed) and a
  summary.

Parsing the information table:
- XML with a DOCTYPE or ENTITY is refused.
- The root must be `informationTable`. The filing's other XML files are tried in name order,
  three at most.
- **Value units.** Filings made before 3 Jan 2023 report `value` in thousands of dollars, and
  later ones in dollars (SEC's amendments to Form 13F, Release 34-95148). The value is converted
  by the **filing date**, so a 2022 quarter amended in 2023 is read in dollars.
  - The summary gives `value_unit` and, with 5 or more share lines, the median value per share.
  - A `unit_warning` flags a filing whose median is below $0.50 or above $100,000. It is
    reported, never changed.
- **Aggregation.** A filer reports one security on several lines (by manager and discretion).
  Lines are summed per CUSIP and put/call.
  - SH and PRN never mix: a PRN line on a CUSIP that is also reported in SH is left out and
    counted.
  - `put_call` is `''` for the security itself.
- **CUSIPs.** A CUSIP is upper-cased with spaces removed. One that lost its leading zeros is
  padded back only when the padded check digit is right, and counted as repaired. Any other bad
  CUSIP, number, share type or put/call drops that line, counted by reason.
- **The cover page** gives the quarter, which must match EDGAR's index and be a quarter end.
  13F notices are skipped.

## Amendments (by accession)

1. Every filing keeps its own accession's rows. A filing's rows are replaced only by itself (a
   rerun or `--refresh`), never by another filing.
2. Readers take each filer's latest accession for a quarter: the newest `filed_at`, then the
   highest `accession`.
3. A 13F-HR/A whose cover says **RESTATEMENT** is a complete report. It is stored as it is and
   supersedes the earlier ones by rule 2.
4. A 13F-HR/A whose cover says **NEW HOLDINGS** only adds lines. It is stored as the holdings of
   the report before it (by rule 2's order) plus the added lines, summed where CUSIP, put/call and
   share type meet. The latest accession is therefore always a complete report.
   - If the earlier report is not stored, the amendment is skipped with a reason, never stored
     half.
   - Filings are read oldest first, so within one run the base is in place.
5. An amendment that says neither is skipped with a reason. So is a 13F notice (13F-NT, or a
   cover whose report type is a notice), which has no information table.

## The held migrations and their day

Each table is its own held migration. The design gives each switch-on **one free day after Wed
25 Nov** (or after 18 Dec if the November days are full), in the order:

1. daily volumes;
2. factor returns;
3. **macro series**;
4. **13F**.

So `macro_series.sql` runs on its own day, and `institutional_holdings.sql` on a later one.

- At merge each draft takes the next free number in `migrations/postgres`. 074 to 077 are taken
  by the drafts already held, and part A's two tables take numbers too.
- On its day, and not before, the draft moves to
  `migrations/postgres/<number>_macro_series.sql` (or `_institutional_holdings.sql`), and in the
  same change `"held"` is dropped from its catalog entry. The undo stays in drafts.
- It runs alone through the bootstrap job with `--only-migration`.

Each migration only adds:

- one table, its view in `public`, its policy, comments and (13F) one index;
- the grants that `bootstrap_postgres.py` gives every public table:
  - the app login: SELECT, INSERT, UPDATE, DELETE;
  - the website login: SELECT;
  - the approval login: SELECT on `observation_date` or `period` only, which the freshness check
    reads.

Nothing existing changes. While a table is absent:

- its freshness rule and volume count are skipped (`held`);
- its contract rules are skipped;
- its job exits 65.

## Switching on

Each step is one PowerShell line, run in a clone of `main`. Every step can be run again. Do not
run steps 2, 7, 8 or 12 between 07:50 and 09:25 UTC (the loaders refuse to start inside it).

**Before the day, in PRs reviewed as usual:**

- **The filer list.** Check every CIK and name in `src/ibkr_etl/data/sec_13f_filers.json` on
  EDGAR, complete it to the 50 largest filers of the latest quarter, then set `reviewed: true`,
  `reviewed_by` and `reviewed_on`.
  - The starter list has 12 filers.
  - BlackRock's CIK needs a check after its 2024 reorganisation.
  - The job refuses an unreviewed list (exit 78).
- **The licences.** Confirm Brent's provenance (EIA's commercial source). Decide VIX: leave it
  out, or record the licence and add `macroSeriesLicenseConfirmed=VIXCLS` to step 4's line.
- **The packaging.** Add `data/sec_13f_filers.json` to `[tool.setuptools.package-data]` in
  `pyproject.toml`. The scripts read it from `/app/src` today, and this keeps an installed copy
  complete.
- **The day's migration.** The draft moves to its number and its `"held"` goes from the catalog,
  as above.

| # | Where | PowerShell line | What it does |
|---|---|---|---|
| 1 | Admin VM, `az login` | `python scripts/medallion_layers.py snapshot --account <pilot account> --out before-macro.json` | The read-only snapshot, as `MEDALLION_S3_RUNBOOK.md` step 2 |
| 2 | Admin VM | The runbook's step 3 line: the bootstrap job once, with `scripts/bootstrap_postgres.py --only-migration <number>_macro_series.sql --allow-pending <held files still pending> --allow-applied-without-file 067_hong_kong_china_external_market_data_approval.sql` | Applies the one migration. Ends `Migrations applied: <number>_macro_series.sql.`, and a second run applies nothing |
| 3 | Admin VM | `python scripts/medallion_layers.py check --before before-macro.json --applied <number>_macro_series.sql --account <pilot account>` | One new ledger row, nothing else changed |
| 4 | Ying's PC | `az deployment group create -g rg-ibkr-database-v11 -f infra/staging-macro-series-job.bicep -p registryName=<registry> keyVaultName=<key vault> identityResourceId=$(az identity show -g rg-ibkr-database-v11 -n ibkrv2stg-ingestion-identity --query id -o tsv) imageDigest=<the staging jobs' digest> macroSeriesLoad=off` | The job, still off |
| 5 | Ying's PC | `az containerapp job start -g rg-ibkr-database-v11 -n ibkrv2stg-macro-series` | Proves the wiring: prints `{"status": "off", ...}` and exits 0, with nothing written |
| 6 | Ying's PC | Step 4's line with `macroSeriesLoad=on` | On |
| 7 | Ying's PC | Step 5's line | The first load, by hand. The summary should be `success`, with each series' `first_date`, `latest_date` and counts, and blobs under `fred-series/`. If it is not, step 4's line turns it off again. From then on each 22:30 run stores only new or revised values |
| 8 | Admin VM, on the 13F day | Steps 1 to 3 with `<number>_institutional_holdings.sql` | The 13F table, alone |
| 9 | Ying's PC | `az deployment group create -g rg-ibkr-database-v11 -f infra/staging-institutional-holdings-job.bicep -p registryName=<registry> keyVaultName=<key vault> identityResourceId=$(az identity show -g rg-ibkr-database-v11 -n ibkrv2stg-ingestion-identity --query id -o tsv) imageDigest=<the staging jobs' digest> secUserAgent='<operator name> <contact address>' institutionalHoldingsLoad=off` | The job, still off |
| 10 | Ying's PC | `az containerapp job start -g rg-ibkr-database-v11 -n ibkrv2stg-institutional-holdings` | Prints `{"status": "off", ...}` and exits 0 |
| 11 | Ying's PC | Step 9's line with `institutionalHoldingsLoad=on` | On. Quarterly from the next 20 Feb, May, Aug or Nov |
| 12 | Ying's PC | Step 10's line | The backfill of the last 4 quarters, by hand. Check the per-filing lines (no `unit_warning`, a plausible `median_value_per_share`, few repaired or invalid lines) and the summary's `failed` and `skipped`. If the time budget stopped it, run it again: stored accessions are skipped |
| 13 | PR | Record both jobs in `deploy/deployed-images.json` | So `deploy/drift_check.py` stays clean |

After a week of runs, the freshness rules can be made required, and the volume counts judged,
in `layer_freshness.RULES` and `layer_quality.VOLUMES`.

## Undoing

- **Lighter:** redeploy with `macroSeriesLoad=off` or `institutionalHoldingsLoad=off`. The rows
  stay. Nothing reads them until the gold sections are switched on by their own setting.
- **Whole:** `macro_series_undo.sql` and `institutional_holdings_undo.sql` each drop their table
  and its public view (with the index, policy, comments and grants), and nothing else.
  - Each undo leaves the other table alone. The tests apply and undo the drafts in either order
    and find nothing left.
  - Run each as its own numbered migration with `--only-migration`, or by psql.
  - Keep a copy first if the rows may be wanted (`pg_dump -t silver.macro_series`).
  - Delete the migration's ledger row by hand if it should run again later.
- **The raw files stay** in `bronze-market-data` (legal hold), so either table can be loaded
  again from bronze or by a rerun.
- Delete the jobs with `az containerapp job delete`. Neither job has a role assignment of its
  own to remove.
