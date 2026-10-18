# Medallion architecture

The platform's data is organised in three layers, with a control plane beside them.
`infra/medallion/catalog.json` records the layer of every database table and view,
blob container and job. `tests/test_medallion_catalog.py` fails when a migration adds a
table or view, or Bicep adds a container, without a catalog entry.

Status: the catalog is in place. Nothing has moved yet, and every table is still in
`public`. The moves happen in stages, each approved separately (see "Stages" below).

## Layers

| Layer | Holds | Rule |
|---|---|---|
| Bronze | IBKR Flex statements and their records, hand-off files, raw market-data responses, live TWS snapshots, uploads (onboarding files and factsheet templates) | Append only, never edited. Data received from IBKR must not be modified, and the same applies to every source. |
| Silver | Accounts, securities, trades, positions, cash, NAV, FX, parsed market data (prices, benchmark prices, FX closes, risk-free rates), sectors, derived and composite portfolios | Each table's `source` in the catalog says how it is rebuilt: `ibkr` tables from bronze (v2's own Flex pull from the switch on; v1.1's history from its archived statements), `market-data` tables once their raw responses are kept in bronze (from S3b), `derived` tables from other silver tables. A record IBKR re-sends replaces the earlier one here; every version stays in the raw statement files. |
| Gold | Performance and benchmark comparison, risk snapshots, crisis results, frozen report payloads | Built from silver. Month-end snapshots never change; risk and crisis tables are rebuilt daily. Known exceptions until S6 and S7: the benchmark comparison reads bronze `flex_rows` for cash flows, and the crisis jobs fetch prices while they compute. The website, renderers and agents read gold, plus the silver read contracts, and never bronze. The one exception is a page or agent reading back a file a person uploaded through it, such as a factsheet template. Each gold figure maps to an entry in the Metrics guide. |
| Ops | Runs, approvals, audit, access, onboarding workflow, configuration and hand-maintained settings (subjects, benchmark list and mapping, metric catalogue), agent state | Control plane, not a data layer. Stays in `public`. |
| Platform | `tenants`, `schema_migrations`, row-level security helpers | Stays in `public`. |

## Where each layer lives

- **Postgres:**
  - Target schemas are `bronze` (14 tables), `silver` (21 tables and 8 read views) and
    `gold` (6 tables).
  - `public` keeps the platform and ops objects, so only the data tables move.
  - The `agent` schema stays as it is: agent state plus account-scoped views over silver
    and gold.
- **Blob storage:**
  - Bronze: `ibkr-raw`, `onboarding-intake`, `input-snapshots` (until v1.1 retires),
    and a planned `bronze-market-data`.
  - Gold: `internal-reports` and `report-drafts`.
  - Ops: the release and audit containers, and a planned `ops-qa`.
  - Containers that database rows point to keep their names and get a layer label.
  - New containers are named `<layer>-<content>`.
- **Jobs:** the catalog records each job's hops (ingest, conform, analytics,
  quality-gate, publish, serve, ops). Job names stay, and a tag carries the hop.

## Adding a table, view or container

Add an entry to `infra/medallion/catalog.json` in the same PR:

- Postgres objects are keyed `schema.name`, with `kind`, `layer`, `target_schema` and an
  optional `note`.
- Containers are keyed `account/container`, with `layer`, `holds` and `plan`.

Choose the layer by what the object holds, not by which job writes it. A raw provider
response is bronze. The table it is parsed into is silver.

## How a table moves

Each batch is one migration:

- `ALTER TABLE public.<name> SET SCHEMA <layer>` keeps the table's data, indexes,
  constraints, row-level security policies, triggers and grants. Views bound to the
  table, such as the agent views, keep working.
- A view with the old name stays in `public` so that existing SQL keeps working. It is
  `security_invoker`, so row-level security and grants apply to the caller.
- The runtime logins list the layer schemas first in `search_path`.
- The migration login keeps `public` first. Otherwise `db.py` would create a second,
  empty `schema_migrations` in a layer schema.
- A migration that changes a moved table names it by its layer and re-creates its
  compatibility view, because a view fixes its column list when it is created.
- A normal bootstrap applies every migration on `main`. So a batch's migration merges
  on the day the batch runs, and that day's bootstrap run applies only it
  (`--only-migration`). [MEDALLION_S3_RUNBOOK.md](MEDALLION_S3_RUNBOOK.md) has the
  steps, the checks and the undo.

## Raw market data and freshness checks

- **Raw capture** (`src/ibkr_etl/raw_capture.py`):
  - Each market-data fetch stores the provider's response, before parsing, in
    `bronze-market-data` as `<feed>/<yyyy>/<mm>/<dd>/<request-key>-<sha256 prefix>.<ext>`.
    It never overwrites a blob.
  - A window that ended over a week ago (a past Treasury year, a crisis window) is
    stored once as `<feed>/history/<request-key>-<sha256 prefix>.<ext>`. Asking again
    finds the existing blob, so nothing new is kept under the legal hold.
  - It is off unless the job sets `MARKET_DATA_RAW_CONTAINER` and
    `AZURE_STORAGE_ACCOUNT_URL`.
  - It is fail-soft:
    - it uses managed identity only, with short timeouts and no retries;
    - every thread shares one time budget, which is a hard limit;
    - the first storage error or timeout turns capture off for the rest of the run,
      and the job carries on.
- **Freshness checks** (`src/ibkr_etl/layer_freshness.py`):
  - The autonomous administrator runs explicit rules for each layer in one READ ONLY
    transaction, within a hard 15-second limit. By default it uses its own
    `DATABASE_URL`; `LAYER_FRESHNESS_DATABASE_URL` overrides it.
  - Results:
    - a late layer raises a `layer_freshness:<check>` warning, which closes only when
      that check comes back fresh;
    - a required check that cannot be read is recorded as info, and becomes a
      warning after an hour.
  - It is off unless `LAYER_FRESHNESS_ENABLED=true`. The `bronze-market-data` file
    checks also need `LAYER_FRESHNESS_BLOB_CHECKS=true`, for once capture is on.
  - `python -m ibkr_etl.layer_freshness` prints the same checks for a dry run.

## Input readiness checks (U3 step one)

Each daily job after ingestion first asks whether its inputs for today's cycle are
ready (`src/ibkr_etl/input_readiness.py`). The jobs and the stage each one produces
are benchmark-daily and sector-classification (benchmarks and FX), derived-daily
(derived), risk-snapshots and crisis-scenarios-daily (risk), month-end-factsheet
(reports) and analyst-assurance (assurance).

- **What it checks.** The dependency policy in `workflow_readiness.py`, applied to
  evidence the earlier steps already leave in the database:
  - ingestion and reconciliation: institutional-daily's statement requests and account
    imports for its newest run date (`institutional_work_items`). They must all have
    succeeded and must cover yesterday, because the pull asks IBKR for data up to
    yesterday;
  - benchmarks and FX: the newest finished benchmark run in `ingestion_runs`. The
    newest sector-classification run in the lineage records (below) can only delay;
  - derived: each subject's newest calculation in derived-daily's newest execution
    (`subject_calculation_runs`); one still running means it has not finished. A failed
    or blocked calculation of a subject that is not live (`platform_subjects` lifecycle
    not `active`, for example still in onboarding) is listed as `waiting` and not
    counted, because risk and reports only process active subjects. A live subject's
    failed or blocked calculation still stops the check;
  - risk: the time the risk job last checked each snapshot (`risk_analytics_snapshots`),
    and the risk job's newest run in the lineage records;
  - reports: month-end-factsheet's newest run in the lineage records, and nothing else
    (it leaves no other record of each daily run).

  These must have succeeded today (UTC), after the steps they read. The expected dates
  go through `workflow_dates`, as a persisted date policy would. If the shadow ledger
  ever has rows for the cycle (`daily:<date>`, subject `daily-cycle`), they must agree
  too. Today it has none: migrations 054 and 060 are held back in production.
- **Lineage records as start and finish.** With `LINEAGE_RECORDING=true`, risk-snapshots,
  sector-classification and month-end-factsheet leave a START row in `lineage_events`
  (migration 071) and one COMPLETE, FAIL or ABORT row when they end. For each of them
  the check reads the newest run:
  - a START with no end, younger than the job's `replicaTimeout`, means still running.
    That only delays: the job is not ready while it waits (see enforce below). If the
    run is still open when the wait is over, the check runs once more as if the run had
    left no record, and that check decides. So a lost end write costs at most the wait.
    This is what holds month-end at 09:00 while the 08:55 risk run is still stamping
    snapshots, and derived until sectors end;
  - COMPLETE, or FAIL from a non-zero return code (the job ran to its end, for example
    sectors returning 2 for unclassified holdings, or month-end returning 1 for a
    partial failure), means finished. The risk evidence is then dated by the run's end,
    and reports by month-end's end;
  - FAIL from an exception (including a readiness stop) or ABORT means not validated,
    for risk and reports;
  - no record, a run that ended before this cycle, a START with no end after the
    `replicaTimeout`, or a table that is missing or unreadable: the record is not used.
    Risk is then judged by its snapshots alone, as before, and reports are not checked
    (assurance is then judged by the steps before reports).

  Sectors are a soft dependency. A sector run still running holds up the jobs after it
  only within the wait. One that failed, crashed or never ended, or that ended after a
  step that reads it, goes in `notes`, never in `reasons`: yesterday's sector
  classifications are good enough for a day. The decision says in `notes` whenever a
  record was not used, and notes never make the inputs not ready.
- **How it runs.** One READ ONLY session with short timeouts
  (`layer_freshness.open_read_only`), within a hard 10-second limit, using the job's own
  `DATABASE_URL` (`INPUT_READINESS_DATABASE_URL` overrides it; that login needs SELECT on
  the evidence tables and `platform_subjects`, and on `lineage_events` for the lineage
  part). A step it cannot read makes the inputs not ready. It never writes, so the
  decision goes to the log, not to the ledger.
- **Modes,** set by `INPUT_READINESS_MODE` on the job:
  - `off`, the default and also when the variable is unset: nothing runs. No query, no
    log line, no wait, no new way to fail. A test proves this.
  - `shadow`: logs one line, `INPUT_READINESS {json}`, with the decision, the reasons,
    the notes and each step's evidence, and the job always carries on. It never waits,
    so an open run shows there as not ready.
  - `enforce`: the same line with `"decision":"stop"`, then the job ends with exit code
    75 before doing anything, so the execution shows as failed. With
    `INPUT_READINESS_WAIT_SECONDS` above 0 (default 0: no wait), a job that is not
    ready first logs a line with `"decision":"wait"`, then checks again every
    `INPUT_READINESS_RECHECK_SECONDS` (default 60, at least 5) until it is ready or the
    wait is over, then once more without open runs if any is left, and logs the final
    line with `attempts`, `waited_seconds` and `first_reasons`. Each check has its own
    session and 10-second limit, so the gate takes at most the wait plus one recheck
    interval and about 30 seconds of checks. The wait is capped at 1800 seconds; a value
    that is not a number means no wait, and the line notes it.
- **Recommended enforce settings.** Every job has `replicaRetryLimit: 0`, so a stop is
  final for the day. The wait covers a step that is a few minutes late; it must leave
  the job most of its `replicaTimeout`. Only derived-daily's run time is on record
  (about 1.5 minutes), so these leave wide margins:

  | Job | Starts (UTC) | `replicaTimeout` | Waits for | Wait | Recheck | Left for the job |
  |---|---|---|---|---|---|---|
  | benchmark-daily | 08:30 | 1200 s | pull (08:20) | 300 s | 30 s | at least 840 s |
  | sector-classification | 08:45 | 1800 s | pull | 600 s | 60 s | at least 1110 s |
  | derived-daily | 08:50 | 3600 s | pull, benchmark, sectors | 900 s | 60 s | at least 2610 s |
  | risk-snapshots | 08:55 | 3600 s | derived, benchmark, sectors | 900 s | 60 s | at least 2610 s |
  | month-end-factsheet | 09:00 | 3600 s | risk | 900 s | 60 s | at least 2610 s |
  | analyst-assurance | 09:10 | 600 s | month-end, risk | 120 s | 30 s | at least 420 s |
  | crisis-scenarios-daily | 09:20 | 1800 s | derived, benchmark, sectors | 300 s | 60 s | at least 1410 s |

  A stop still passes down the chain: a job that stopped leaves its stage stale, so the
  jobs after it stop too, as they should. Sectors are the exception: they never stop
  anything.
- **Runs started from the website.** The onboarding page starts derived-daily outside
  the daily chain. When the job is set to enforce, those executions get
  `INPUT_READINESS_MODE=shadow` for that execution only (`admin_actions.start_job_with_env`
  reads the job and passes Azure's start call the job's own template with that variable
  added), so they never wait or stop. While the job is off or in shadow, it starts as
  configured, as before. If the job cannot be read, the template cannot be copied
  exactly, or Azure refuses it, the job starts as configured and the web log says so.
- **Switching it on** is a separate approved step: add `INPUT_READINESS_MODE` (and for
  enforce the wait settings) to the seven job definitions, with `LINEAGE_RECORDING=true`
  on risk-snapshots, sector-classification and month-end-factsheet, on an image built
  from a `main` that has this code. To read the results:
  `ContainerAppConsoleLogs_CL | where Log_s startswith 'INPUT_READINESS '`. Explain
  every "not ready". The evidence queries are judged on synthetic data only so far.
- `python -m ibkr_etl.input_readiness <job>` prints a dry run of the check.

## Release data report

A release that touches silver or gold runs its jobs on a restored copy first, and
`scripts/release_data_report.py` compares every silver and gold table before and after:
rows added, removed and changed, and the largest changes per table and per account or
subject. Its public summary (counts and names only) goes on the PR; the full report,
with the figures, goes to the approver through the project's private folder.
[MEDALLION_RELEASE_REPORT.md](MEDALLION_RELEASE_REPORT.md) has the steps.

## Stages

1. **S0:** this catalog and test (repo only).
2. **S1:** keep all raw data. Copy the v1.1 raw statements into `ibkr-raw`, add the new
   containers and their labels, and add container-scoped storage roles next to the
   current ones.
3. **S2:** keep raw copies of market-data responses, and add freshness checks for each
   layer.
4. **S3:** move the tables in three batches (gold, bronze, silver), rehearsed first on a
   restored copy.
5. **S4:** narrow access by layer once storage logs show nothing needs the wider roles.
6. **S5:** v2 makes its own Flex pull into bronze with v1.1's query, and v1.1 stops. The
   switch is rehearsed first on a restored copy of the database, with v2's loader run over
   v1.1's archived statements and compared with what was imported.
7. **S6:** typed silver views, a replay that rebuilds silver from bronze, and promotion
   gates recorded in the workflow evidence ledger.
8. **S7:** gold tables for every Metrics guide figure, so each surface reads the same
   stored number. The factsheet design bindings (`src/ibkr_etl/factsheet_spec/bindings.py`)
   already name a Metrics guide entry for every figure a design can print, so they are the
   list of figures the factsheet surface needs from gold.
