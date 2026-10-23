# Medallion architecture

The platform's data is organised in three layers, with a control plane beside them.
`infra/medallion/catalog.json` records the layer of every database table and view,
blob container and job. `tests/test_medallion_catalog.py` fails when a migration adds a
table or view, or Bicep adds a container, without a catalog entry.

Status: the tables are in their layer schemas. Migrations 068, 069 and 070 (S3) moved
them into `gold`, `bronze` and `silver`, each leaving a `security_invoker` view of the same
name in `public`. 082 adds Kiwoom's stored answers (`bronze.kiwoom_responses`) when it is
applied. Each stage is approved separately (see "Stages" below).

## Layers

| Layer | Holds | Rule |
|---|---|---|
| Bronze | IBKR Flex statements and their records, hand-off files, raw market-data responses, Kiwoom REST answers (from migration 082), live TWS snapshots, uploads (onboarding files and factsheet templates) | Append only, never edited. Data received from IBKR must not be modified, and the same applies to every source. |
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
- **Azure resources:** each one carries `layer` (foundation, data, apps, ai or ops),
  `component` and `owner` tags, and jobs and apps also carry `hop`.
  `infra/medallion/applied-tags.json` records what was added on 17 Oct 2026. A Bicep file
  that deploys one of those resources sets the same tags with `union(tags, {...})`, so a
  redeploy keeps them. `tests/test_bicep_medallion_tags.py` checks this. A new resource
  in a Bicep file gets its three tags in the same PR. The resource group's audit rule
  (Azure Policy, "Medallion rule") reports any resource without them.

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
  - By default it is fail-soft:
    - it uses managed identity only, with short timeouts and no retries;
    - every thread shares one time budget, which is a hard limit;
    - the first storage error or timeout turns capture off for the rest of the run,
      and the job carries on.
  - With `MARKET_DATA_RAW_REQUIRED=true` the raw copy is part of the fetch:
    - a response whose copy cannot be stored raises `RawCopyNotStored`, so its fetch
      fails before anything is parsed and nothing from it reaches silver;
    - each copy gets three tries (pauses of 1 and 3 seconds) with longer timeouts and no
      time budget, and a file already there counts as kept;
    - once one copy has failed every try, the rest of the run's fetches fail at once;
    - without `MARKET_DATA_RAW_CONTAINER` and `AZURE_STORAGE_ACCOUNT_URL` every fetch
      fails, so the setting only goes on jobs that have both.
- **Rebuild check** (`src/ibkr_etl/market_data_rebuild.py`,
  `scripts/check_market_data_rebuild.py`): read-only. It rebuilds the recent rows of
  `silver.benchmark_prices` (each row names its raw file by `raw_sha256`) and of the U.S.
  Treasury series in `silver.risk_free_rates` (the newest stored year file) with the jobs'
  own parsers, and prints one `MARKET_DATA_REBUILD` line with counts: matched, differing
  (by id and date, never values), missing from the file, and rows whose file was not
  found. It exits 1 when a row differs (`--require-files` also fails on a missing file).
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

## Flows from silver (S6): `FLOWS_SOURCE`

Every return figure takes its deposits and withdrawals from one reader,
`provisional_benchmark._external_flows`: the website pages (through
`web._subject_external_flows`, which the certified build and Modelling call too), the
benchmark comparison, the monthly reports, the month-end snapshots and the website analyst's
tools (`agent_tools`), the agent tool server and composite sleeves. For a physical account it
reads bronze: the Transfers and CashTransactions rows of `flex_rows`, the known exception in
the Gold row above. Filtered and composite portfolios read their silver cash ledger and are
not affected. `ibkr_etl.flows_source` adds the silver reader and the switch.

- **The setting:** `FLOWS_SOURCE`, set to the same value on every container app and job that
  serves or builds return figures (the website, the certified build, derived-daily, the
  monthly and month-end jobs).
  - `off`, the default (also unset, or a value not understood, which logs one line once):
    bronze, exactly as before.
  - `fallback`: both are read. Silver is used only when it equals bronze on every date: the
    same dates, the same amount per currency on each date (summed exactly), and the
    base-currency total within a cent. Otherwise bronze is used and one line is logged,
    `FLOWS_SOURCE {"dates_differing":1,"decision":"bronze","kinds":{"transfers_not_in_silver":1},"mode":"fallback","reason":"silver_differs","subject":"u…01"}`
    (`reason` is `silver_differs` or `silver_unreadable`; counts and masked subjects only). To
    read them: `ContainerAppConsoleLogs_CL | where Log_s startswith 'FLOWS_SOURCE '`.
  - `on`: silver only. Not before two clean month-ends in `fallback`, and not before the
    Transfers section has a silver table (below).
- **What silver holds.** The loader writes each CashTransactions record to `flex_rows` and to
  `silver.cash_transactions` in the same loop, under the parser's key. The daily 14-day
  re-send updates the same row in both; a correction under the same key overwrites both; a
  correction under a new key (no IBKR transaction id) adds a row to both. With
  `IBKR_FLEX_LEGACY_KEY_COMPAT` silver keeps v1.1's key, so the comparison matches rows by
  content, never by key. Two gaps keep bronze in `fallback`: silver has no table for the
  Transfers section (the loader writes it to `flex_rows` only), and it cannot tell a
  consolidated copy (`accountId` "-") from the account's own row, which bronze skips.
- **Leave the agent tool server unset** until the agent schema has a view over silver flows:
  its login reads only the `agent` views, so `fallback` there always logs
  `silver_unreadable` and keeps bronze (the failed read is inside a savepoint). Removing
  `agent.flex_rows` comes after that.
- **Undo:** remove the setting. Nothing is written anywhere.
- **Before switching on, on a restored copy:**
  `python -m ibkr_etl.flows_source compare --database-url "<copy URL>" --tenant <tenant id>`
  reads every subject over all history in READ ONLY transactions and prints counts: subjects,
  accounts, matching, differing, errors, the kinds of difference (subjects and dates each),
  and notes that explain them (`bronze_transfer_rows`, `bronze_consolidated_copies`,
  `silver_rows_sharing_a_transaction_id`). It must show no errors, and every differing
  account explained by its notes, for example an account-sync account whose bronze flows were
  never backfilled (`only_in_silver`). Matching accounts are the ones `fallback` reads from
  silver; the others keep bronze and log one line on every read.
- **Before `on`:** two clean month-ends in `fallback`; a silver table for Transfers (a
  migration, the loader writing it, and a fill from `flex_rows`), or proof that no account
  has Transfers rows; and the agent view above.

## Release data report

A release that touches silver or gold runs its jobs on a restored copy first, and
`scripts/release_data_report.py` compares every silver and gold table before and after:
rows added, removed and changed, and the largest changes per table and per account or
subject. Its public summary (counts and names only) goes on the PR; the full report,
with the figures, goes to the approver through the project's private folder.
[MEDALLION_RELEASE_REPORT.md](MEDALLION_RELEASE_REPORT.md) has the steps.

## Every source goes through bronze

The layering covers the whole resource group (decided 19 Oct 2026): every source lands in
bronze exactly as received before anything reaches silver, and silver is rebuilt from
bronze alone. Where a source still writes silver straight from the live answer, this is
the plan that closes the gap.

| Source | Bronze | Silver written by | Plan |
|---|---|---|---|
| IBKR Flex | `ibkr-raw` files, `bronze.raw_reports`, `bronze.flex_rows` | The loader, from bronze (S5, 17 Oct) | Done. |
| IBKR live (TWS) | `bronze.live_*` | Not used for silver | Done. |
| Kiwoom REST | `bronze.kiwoom_responses`, every page since the 22 Oct daily run (Korea and the U.S. account) | `ibkr_etl.kiwoom_collector` (Korea, KRW) and `ibkr_etl.kiwoom_us_account` (the U.S. account, USD), from the live answer | `bronze.kiwoom_responses` (migration 082, applied alone after the U8 record). The collector stores every page first (`KIWOOM_BRONZE=true`, `ibkr_etl.kiwoom_bronze`). The conform step `ibkr_etl.kiwoom_conform` (`scripts/run_kiwoom_conform.py check --run-id N` or `--latest`) replays one bronze run's stored answers through the collector's own functions, never calling Kiwoom, and compares the rows with silver by the collector's keys. It is read-only and prints counts only (matched, differing, only in the rebuild, only in silver, missing answers, refusals replayed). With `KIWOOM_BRONZE_REQUIRED=true` as well, storing each page is part of the call, as with market data: a page bronze cannot store after three tries (or an account whose bronze run cannot be opened) fails that account before the answer is used, its uncommitted silver rows roll back, and every later account fails before Kiwoom is called, so silver only ever holds rows built from answers already in bronze. After such a run the collector rebuilds each loaded account's bronze run and compares it with silver (`kiwoom_rebuild`, one more line of counts, read-only). A failed day is filled by the next daily run, which lists the movements again in full and fills any missing past day. The past days silver held before capture began are asked for again into bronze by `scripts/run_kiwoom_bronze_history.py` (`ibkr_etl.kiwoom_bronze_history`). It is run by hand in the daily job's place, outside the daily run and Kiwoom's 16:00–18:00 and 19:00–22:00 UTC rebuild. It makes the collector's own calls for each loaded account's silver days that bronze lacks, writes bronze only (its account reads are read-only), and checks each run with the conform step. The silver contracts state the rules both accounts' rows keep: the total adds up on every Kiwoom row, rows are in the account's own currency, the U.S. account's positions add up to Kiwoom's securities value and its cash rows to its NAV cash, and trades and flows carry the right direction. Two freshness checks watch Kiwoom: `bronze.kiwoom_responses` (an answered page each cycle) and `silver.nav_snapshots.kiwoom` (each Kiwoom account's newest NAV, so one account falling behind shows). Both are reported only until the administrator's login can read bronze and a week of readings is fresh. Each account's history is fetched again into bronze and silver rebuilt from it; any difference is kept as a restated version (U8) with a note. A past day is Kiwoom's daily balance (`ka01690`) with its account return for that day (`kt00016`, Kiwoom's own value of the holdings) and, when `ka01690` lists after-market prices, each stock's official KRX close, read as the next trading day's base price on the unadjusted daily chart (`ka10081`), so a day's close is known from the next trading day; all three are kept in bronze. `kt00016` counts a trade only once it settles, two trading days later, while `ka01690` counts it on the trade date; on a day with trades not yet settled at the close the two give different cash, and the day is `ka01690`'s alone, kept only when its holdings, cash and Kiwoom's per-stock selling cost add up to its total within 1 KRW a holding (at the official close when `ka01690` lists after-market prices), and otherwise failed. Silver checks each Kiwoom day to the won: the positions add up to Kiwoom's holdings value, and the selling cost follows Kiwoom's per-stock rule. History fetches run outside 16:00–18:00 and 19:00–22:00 UTC, when `ka01690` answers with empty figures; the 08:00 UTC daily run is unaffected. Open quality exceptions hold that account's reports until the owner accepts them by name. |
| Market data | `bronze-market-data` files since 18 Oct, best effort | The fetchers, from the live answer | Next, a fetch whose raw copy cannot be stored fails (`MARKET_DATA_RAW_REQUIRED` on the benchmark, risk-free, sector and crisis jobs), the rebuild check compares silver with what the stored files parse to, and the bronze file freshness checks turn on (`LAYER_FRESHNESS_BLOB_CHECKS`). Nothing raw exists before 18 Oct. When Yahoo's history reply for a benchmark ends before the session a subject needs (on 22 Oct 2026 Yahoo served Azure the 21 Oct bars with no close), benchmark-daily reads that one close from Yahoo's quote, stores the quote reply in `yahoo-benchmark-history` next to the history file, and records the row with the quote's own `source_reference` and `raw_sha256` plus a `benchmark_close_from_quote` warning. `BENCHMARK_QUOTE_FALLBACK=off` turns it off. |
| One-off scripts | None | Hand-run backfills and cut-overs in `scripts/` | Only the named loaders' login writes silver (see [Silver write lock](#silver-write-lock-step-3)), and a CI check refuses new code that writes silver anywhere else. |

The Kiwoom table keeps inquiry answers only. Token calls are never stored, and
`request_key` holds the account number and query fields, never a key, secret, token or
header. Like every bronze table, the website, renderers and agents never read it.

## Silver write lock (step 3)

Only the named loader jobs write silver. Three parts, each with its own undo:

1. **Watch** (migration 083). A statement trigger on every silver table but the U8 history
   notes each write by a login outside the `silver_loader` group in
   `public.silver_write_watch`: the login, the table, the kind of write, the lineage run id
   and the application name, never row values. It refuses nothing. The tables' owner
   (migrations, the approved FX step) is not noted. Undo:
   `infra/medallion/undo/083_silver_write_watch_undo.sql`.
2. **Loader login.** With `DATABASE_LOADER_PASSWORD` (and `DATABASE_LOADER_ROLE`) set,
   `bootstrap_postgres.py` keeps a loader login that is a member of the app login, so it may do
   everything the app login does, and of `silver_loader`, which may write every silver table
   and the public view over it. The loader jobs read their database URL from the
   `database-loader-url` secret; every other job keeps `database-app-url`. Once they have
   moved, the watch stays empty through a full daily cycle before the next part.
3. **Lock.** Bootstrap with `SILVER_WRITE_LOCK=on` takes INSERT, UPDATE, DELETE and TRUNCATE
   on silver (tables, their public views, and tables made later) from the app login, and
   refuses to finish if any login other than the loader can still write silver. Reading is
   unchanged. Every later bootstrap run must carry the setting; bootstrap without it gives the
   app login its write rights back, which is the undo.

`src/ibkr_etl/silver_writers.py` names the loader jobs and every file that writes silver,
and `tests/test_silver_writers.py` fails on code that writes silver outside that register.

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
