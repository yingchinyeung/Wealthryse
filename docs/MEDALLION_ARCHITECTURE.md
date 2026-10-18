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
(derived), risk-snapshots and crisis-scenarios-daily (risk), and month-end-factsheet
(reports).

- **What it checks.** The dependency policy in `workflow_readiness.py`, applied to
  evidence the earlier steps already leave in the database:
  - ingestion and reconciliation: institutional-daily's statement requests and account
    imports for its newest run date (`institutional_work_items`). They must all have
    succeeded and must cover yesterday, because the pull asks IBKR for data up to
    yesterday;
  - benchmarks and FX: the newest finished benchmark run in `ingestion_runs`;
  - derived: each subject's newest calculation in derived-daily's newest execution
    (`subject_calculation_runs`); one still running means it has not finished;
  - risk: the time the risk job last checked each snapshot (`risk_analytics_snapshots`).

  These must have succeeded today (UTC), after the steps they read. The expected dates
  go through `workflow_dates`, as a persisted date policy would. If the shadow ledger
  ever has rows for the cycle (`daily:<date>`, subject `daily-cycle`), they must agree
  too. Today it has none: migrations 054 and 060 are held back in production.
- **How it runs.** One READ ONLY session with short timeouts
  (`layer_freshness.open_read_only`), within a hard 10-second limit, using the job's own
  `DATABASE_URL` (`INPUT_READINESS_DATABASE_URL` overrides it). A step it cannot read
  makes the inputs not ready. It never writes, so the decision goes to the log, not to
  the ledger.
- **Modes,** set by `INPUT_READINESS_MODE` on the job:
  - `off`, the default and also when the variable is unset: nothing runs. No query, no
    log line, no new way to fail. A test proves this.
  - `shadow`: logs one line, `INPUT_READINESS {json}`, with the decision, the reasons
    and each step's evidence, and the job always carries on.
  - `enforce`: the same line with `"decision":"stop"`, then the job ends with exit code
    75 before doing anything, so the execution shows as failed. For after month-end
    (S6); nothing sets it today.
- **Switching shadow on** is a separate approved step: add
  `INPUT_READINESS_MODE=shadow` to the six job definitions, on an image built from a
  `main` that has this code. To read the results:
  `ContainerAppConsoleLogs_CL | where Log_s startswith 'INPUT_READINESS '`. Run shadow
  for at least two weeks and explain every "not ready" before enforce. The evidence
  queries are judged on synthetic data only so far.
- `python -m ibkr_etl.input_readiness <job>` prints a dry run of the check.

## Layer quality checks (U7)

The autonomous administrator measures each layer against targets and keeps every result
(`src/ibkr_etl/layer_quality.py`). It reports only: it takes no action, and nothing it
finds stops a job or holds back publication. Only the publish gate does that.

- **Targets,** defined once in `TARGETS`:
  - bronze ready by 08:45 UTC, silver by 09:00 and gold by 10:00, on 99% of business
    days. A business day is a New York session; its data is due the next day, so a
    Saturday target covers Friday, and Sunday and Monday have none. "Ready" means the
    freshness checks show that day's data (statements, NAV, positions, benchmarks;
    performance, risk and crisis results);
  - silver and gold checks pass, with no failed check of severity error, on 95% of
    business days.
- **What runs.**
  - Every run (every 5 minutes): readiness, from the freshness results. When
    `LAYER_FRESHNESS_ENABLED` is on it reuses them, so no new query.
  - Once a day, on the first run from 10:30 UTC (after the gold target):
    - row counts for every silver and gold table in the catalog. Dated tables are
      counted per date for six weeks, and the newest complete date is compared with
      the typical count for the same kind of day (both markets open, Hong Kong only or
      New York only);
    - each table's live schema against its data contract;
    - two value checks: a NAV move of more than 10% between a subject's two newest
      dates, and positions dropping to zero;
    - the contract quality rules, apart from any the publish gate already ran that day.
- **Avoiding false alarms.**
  - A count is judged only once there are 10 earlier days of the same kind. It fails
    only when it is under half of both the six-week and the last-five-day typical
    counts, so a backfill or a new account does not trip it.
  - A jump (over twice the typical count, plus two) is a warning, for the report only,
    such as the month-end jump. Event tables (trades, cash and FX transactions,
    corporate actions, derived ledger and exclusions) are recorded, not judged.
  - The value checks need 10 dates per subject, and skip a subject with no NAV for a
    week, such as a closed account.
  - Positions to zero applies only to subjects that held positions on at least 80% of
    their NAV dates. It is reported once, on the day positions drop to zero.
  - A NAV move is a warning: it can be a deposit or a withdrawal.
- **History.** One row per result in `quality_check_results` (migration 072): readiness,
  freshness, counts, schema, value and rule results. The table is append-only and kept
  per tenant. A table rather than Log Analytics, because the weekly report reads 13 weeks
  of it and the job has no way to query Log Analytics. Each run also logs one line,
  `LAYER_QUALITY {json}`, with states and check ids, no subjects and no figures.
  Readiness is recorded once when a layer is first seen ready, and once if it is late at
  its target time. That is what "silver ready by 09:00" is measured from.
- **Report.** `python -m ibkr_etl.layer_quality report [--week-ending YYYY-MM-DD]
  [--json]` states each target as met or missed over the last 13 weeks and the week, then
  the week's failed checks. It gives counts, check ids and table names only.
- **Modes,** set by `LAYER_QUALITY_MODE` on the administrator job:
  - `off`, the default and also when the variable is unset: no query and no log line;
  - `shadow`: measure, record and log. Findings are info incidents, recorded quietly and
    never announced;
  - `warn`: a late layer or a failed check of severity error becomes a warning, and is
    announced once.
- **How it runs.** The checks use a read-only session (`LAYER_QUALITY_DATABASE_URL`, else
  the job's `DATABASE_URL`). The history is written on its own session, apart from the
  administrator's transaction. It all runs within a hard 45-second limit, with 30
  seconds for the daily pass. Whatever is unfinished is recorded as `unavailable`.
- **Switching on** is one approved step, in two parts:
  1. Apply 072 with `bootstrap_postgres.py --only-migration 072_quality_check_results.sql`.
     This also gives the approval login one column of each counted table and
     SELECT and INSERT on the history.
  2. Set `layerQualityMode=shadow` in `infra/staging-autonomous-admin.bicep`.

  Without 072 the checks still run and log, but nothing is recorded, and the daily pass
  runs only in the 10:30 to 10:35 window.
- **Caveats.**
  - The contracts (`infra/medallion/contracts`) are not in the image, so in Azure the
    schema and rule checks report `unavailable` until they ship or
    `LAYER_QUALITY_CONTRACTS_DIR` points at them.
  - The approval login cannot read the tables behind 94 of the 181 rules. Those rules
    are `unavailable`, which is never a finding.
- **Publish gate.** It records the rules it runs with `layer_quality.record(...,
  source="publish_gate")` and `rule_check_id()` ids. The monitor then skips those rules
  that day, and the report lists the gate's failures alongside the monitor's.
- `python -m ibkr_etl.layer_quality` prints a dry run (readiness and the daily pass, now)
  and records nothing.

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
