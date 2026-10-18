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

## Publish gate for gold (U2)

Each gold writer checks the version it has just written before it commits it
(`src/ibkr_etl/publish_gate.py`). Gold is written in place today (upserts by key, or
delete and insert per subject and date), so the commit is the moment a version becomes
what readers see. The gate runs just before it, on the writer's own connection.

- **Where.** `publish_gate.before_commit` is called right before the commit in
  `provisional_benchmark` (benchmark comparisons, per subject), `crisis_replay` (both
  writers), `crisis_scenarios.refresh` and `risk_snapshots.build_risk_snapshot`. These
  run in benchmark-daily, derived-daily, risk-snapshots and crisis-scenarios-daily. The
  month-end packages are written by subprocesses, so month-end-factsheet calls
  `publish_gate.report` afterwards, which judges and logs only.
- **How it runs.** Inside a savepoint that is always rolled back, READ ONLY, with a
  2-second lock timeout and each statement limited to what is left of a 30-second budget.
  The writer's transaction is left exactly as it was. The version is the rows the writer
  just wrote: the subject and write stamp for comparisons, the subject and holdings date
  for the crisis tables, the subject, date and version for risk snapshots.
- **Checks and tolerances** (from the code that already compares these figures):

  | Check | Table | Fails on | Only warns on |
  |---|---|---|---|
  | contract | every gold table | an error rule from its data contract (`publish_gate_rules.json`, generated from the contracts; a test keeps them equal) | a warning rule |
  | nav_matches_ibkr | comparisons | a NAV missing from, or different from, the books' NAV for that day (`subject_nav_series`, read as the writer reads it; rounding as `comparison_source_evidence`: max(1e-8, 1e-10 × value)) | books with more than one, or an unreadable, NAV; IBKR days with TOTAL plus ALL or blank rows (the `v_daily_nav` overlap) |
  | cash_plus_positions | comparisons | never | positions plus cash more than 1% (and at least one unit) from the NAV on the latest day, the export agent's tie |
  | returns_continuity | comparisons | a series that restarts, does not chain, or has a daily return beyond ±100% (portfolio) or ±50% (benchmark), as `build_comparison` | days with a NAV and a benchmark price missing from the series; older rows left inside the rebuilt range |
  | benchmark_coverage | comparisons, risk snapshots | a subject with a NAV but no primary comparison; a risk snapshot with no comparison for its benchmark | a missing secondary; a comparison more than 4 days behind the NAV |

  A check that cannot run (a timeout, a lost connection, a rule that errors) warns, so a
  slow database never holds back a good version. Staleness only warns because the
  writers already hold the primary benchmark to its exchange calendar (weekends, HK and
  other holidays), which a day count cannot.
- **Modes,** set by `PUBLISH_GATE_MODE` on each job:
  - `off`, the default and also when unset: nothing runs. No query, no log line. A test
    proves this.
  - `shadow`: logs one `PUBLISH_GATE {json}` line per version (verdict, reasons, each
    check's evidence) and the writer commits.
  - `enforce`: the same, but a failing comparison or crisis-scenario version is rolled
    back and `BuildHeldBack` raised, so readers keep the last good version: the
    comparison series keeps its earlier rows, and `crisis_scenarios.load` picks the
    latest earlier holdings date. benchmark-daily carries on with the other subjects,
    lists them under `held_back_by_publish_gate`, keeps its run a success (so the next
    jobs' readiness is not stopped) and exits 65 at the end. derived-daily and
    crisis-scenarios-daily treat a hold as that subject's failure, as they treat any
    other. Risk snapshots and the crisis replay are never held back: their pages show one
    exact date, and would show "being prepared" instead of the previous figures. Their
    failures are logged with `not_held_back`. The month-end packages are never stopped.
- **Switching on** is one setting per job: add `PUBLISH_GATE_MODE=shadow` (or `enforce`)
  to the job definition, on an image built from a `main` that has this code. No
  migration is needed. To read the results:
  `ContainerAppConsoleLogs_CL | where Log_s startswith 'PUBLISH_GATE '`.
- A held-back comparison also skips that subject's later steps in derived-daily, and the
  risk job then has no comparison for the new NAV date. The risk page shows "being
  prepared" for that date, as it does today when a benchmark price is late.
- `python -m ibkr_etl.publish_gate <job>` prints a dry run for everything the job wrote
  today (UTC), in its own READ ONLY session (`--since`, or `--run-id` for a lineage run).
  `--write-rules` regenerates the rules file after a contract changes.

### The version readers see (design for S6)

Deciding before the commit is enough for tables whose readers fall back to earlier rows.
To publish through a pointer, so that undoing a build is pointing back and every table
can be held back, S6 needs these changes. Each changes what readers or `SELECT *` see, so
none is built yet:

1. Give each gold build an id (the lineage run id where there is one) and add it to the
   table keys, so a new version sits beside the old one. `crisis_replay_daily` also needs
   `calculation_version` in its key: today a new version overwrites the old.
2. Add `gold.published_builds` (tenant, table, subject, build id, published_at, verdict).
   The gate writes a row only after a pass; a hold leaves the previous row in place.
3. Point the `public.<table>` compatibility views at each subject's published build.
   Readers already read those views, so most need no change. Some read with `SELECT *`
   (`alpha_world_t_crisis.py` on the replay components, and the MS and T qualification
   and audit scripts on comparisons and risk snapshots); give them column lists first.
4. Make the risk page and the crisis replay fall back to the latest published build on
   or before the date shown, then set `holds_back` for those tables.
5. Prune builds that were never published, and builds older than the retention period.
6. Keep month-end report-only: the frozen packages already carry their own sha256.

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
