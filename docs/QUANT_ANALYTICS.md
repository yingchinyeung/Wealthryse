# Quant analytics in gold (`gold.quant_analytics_snapshots`)

Wiring design sections 1 and 2 (`frontier-analytics/wiring-design.md`, 18 Oct 2026), as built.
Everything here is **off**: the table is a held migration that no bootstrap or job applies, and the
job does nothing unless `QUANT_ANALYTICS_BUILD=on`. Nothing changes live until the switch-on steps
below are run, in order, on their day.

| Part | File |
|---|---|
| Held migration (076 on Wed 11 Nov) and its undo | `infra/medallion/drafts/quant_analytics.sql`, `quant_analytics_undo.sql` |
| The Desk page's two silver views, a separate held migration (077) and its undo | `infra/medallion/drafts/desk_live_views.sql`, `desk_live_views_undo.sql` |
| Loaders, builders, writer, reader | `src/ibkr_etl/quant_snapshots.py` |
| The job | `scripts/build_quant_analytics.py`, `infra/staging-quant-analytics-job.bicep` (`ibkrv2stg-quant-analytics`, 09:30 UTC) |
| Contracts | `infra/medallion/contracts/gold/quant_analytics_snapshots.yaml`, `silver/subject_live_executions.yaml`, `silver/benchmark_live_quotes.yaml` |
| Tests | `tests/test_quant_snapshots.py` (no database), `tests/test_quant_snapshots_db.py` (PostgreSQL) |

## What the table holds

One row per tenant, subject, NAV date, **section** and analytics version
(`quant-analytics-v1`), so a section that cannot be computed never holds back the others.

| Column | What it is |
|---|---|
| tenant_id | Row security filters on it (`tenant_isolation`, forced), as on every gold table |
| subject_id | An IBKR account number, or a portfolio or composite slug |
| as_of_date | The NAV date the section is as of (`yyyy-MM-dd`) |
| section | One of the nine below (a CHECK) |
| analytics_version | `quant_snapshots.QUANT_ANALYTICS_VERSION`; part of the key |
| model_id, model_version | The model card (`ibkr_etl.model_cards`) and its engine's `MODEL_VERSION` |
| source_data_sha256 | sha256 of the section's canonical inputs and the versions; an unchanged one is not rebuilt |
| payload_sha256, payload_json | The section payload and the sha256 of its canonical JSON |
| quality_status | `pass` (every engine call worked), `warn` (the section or one call is unavailable, or a number that was not finite was stored as null), `fail` (never shown) |
| generated_at, duration_ms, timings_json | When it was written, how long the section took, and the load, prepare and build times |
| lineage_run_id | The recorded job run that wrote the row (071's trigger) |

Every payload has the same keys: `section`, `model_id`, `model_version`, `as_of`, `available`,
`reason` (why it is unavailable, else null), `inputs` (a short summary: observations, window,
holdings count) and `results`. `results` holds each engine's own output, unchanged, under the name
of the call, so page code reads the fields each engine documents. A call that fails with the
engine's `ModellingInputError` is stored as `{"available": false, "reason": "<its message>"}` under
its name; the section is available when at least one of its calls worked.

The table is created in public and moved into gold with a `security_invoker` view of the same name
left in public, as 073 did. The reader, `quant_snapshots.read_section(connection, *, tenant_id,
subject_id, section, as_of=None)`, takes the latest row of this analytics version on or before the
date asked for whose quality is not `fail`. A day that was held back or failed therefore shows the
day before.

## The sections

Inputs are read from silver and gold only, never bronze, with the same reads the risk snapshot and
the Modelling page use today. Money is in the subject's reporting currency, converted with
`reference_fx_rates` (a rate at most four days old); holding returns use each holding's own
daily marks.

| Section | Page, card (model_id) | Engine calls (`results` keys) | Inputs |
|---|---|---|---|
| `skill` | Performance, skill-statistics | `skill_summary`: daily portfolio against the primary benchmark, the risk-free rate as a daily rate, one trial, level 0.90, 2000 bootstrap samples (seeded, so a rerun gives the same answer) | `performance_comparison_daily` on the primary benchmark (`benchmark-comparison-v1`, pass or warn), the latest USD `risk_free_rates` |
| `decisions` | Performance, decision-attribution | `trade_value_added` (5, 21 and 63 sessions), `skill_profile` (21 sessions), `timing_cost` (decision price = the prior close), `counterfactual_portfolios` (no trades since 1 Jan, and the benchmark) | The last year of `trades` (physical accounts), the daily marks and holdings from `subject_position_series`, `subject_nav_series`, external flows |
| `currency` | Performance, attribution | `currency_attribution` for month to date and year to date (`mtd`, `ytd`), opening holdings grouped by currency, cash and the benchmark as their own segments | Holdings at the period start, marks and FX to the date, NAV, the benchmark levels |
| `tail` | Risk, tail-risk | `evt_var_es` (0.99, 0.995), `tail_backtest_report` (0.975, 0.99), and `forecasts`: today's one-day VaR and ES, stored so later calibration can read the history | The daily portfolio returns (EVT needs about 300, the backtest 500) |
| `regime` | Risk, stress-engine | `regime_model` (risk_forecasting's state probabilities, summarised) and `regime_risk` | Up to 1260 daily portfolio returns (at least 126) |
| `holdings_risk` | Risk, holdings-risk | `ledoit_wolf_covariance`, `ex_ante_risk` (contributions by holding; they add up to the total), `statistical_factors` (3) | Weights at the latest snapshot on or before the date (reporting value over NAV); 252 daily returns per holding |
| `correlation` | Risk, dynamic-correlation | `dcc_correlation` on the five largest holdings (10 pairs) | As holdings_risk (DCC needs 250 observations) |
| `concentration` | Risk, crowding | `concentration` by holding and by sector, `correlation_clusters` (threshold 0.7) | As holdings_risk, plus `instrument_classifications` (classified, pass or warn) |
| `execution` | Modelling, execution-analysis | `implementation_shortfall` per order (fills of one instrument, side and day; decision = arrival = the prior close, final = the day's close), `by_month`, and `skipped` (orders it could not price) | The last year's trades, at most the 400 latest orders; the daily marks |

Who is covered: physical accounts and derived (non-profiled) subjects. The Alpha World MS and T
and profiled composites report through their own HKD pipelines, so their nine sections are stored
as unavailable with that reason. Trades are stored for physical accounts only, so a derived
subject's `decisions` and `execution` are unavailable, with that reason. A subject with no primary
benchmark comparison has `skill`, `tail` and `regime` unavailable.

## The job

`scripts/build_quant_analytics.py` (job `ibkrv2stg-quant-analytics`) at **09:30 UTC**, after the
risk snapshots (08:55) and crisis step (09:20), and over by 09:40, before the second certified run
(09:45).

- **Off by default.** Unless `QUANT_ANALYTICS_BUILD=on` it prints `{"status": "off", ...}` and
  exits 0 without connecting or recording a lineage run.
- **What it builds.** The subjects the risk snapshot job builds (active, benchmark refresh
  registered, with NAV), each for its latest NAV date. `--subject ID` (repeatable) names the
  subjects instead; `--since YYYY-MM-DD` adds every NAV date from that day (a bounded backfill).
  The newest dates go first.
- **One subject and date at a time.** Each is built under its own advisory lock (a date another
  worker holds is left to it and reported as blocked) and in **one transaction**: the nine
  sections are written, the publish gate is called, then the commit. A reader never sees a
  half-written day. With `PUBLISH_GATE_MODE=enforce`, a failing day is rolled back whole and the
  job exits 65 at the end; readers keep the day before (`holds_back`). An unchanged section (same
  fingerprint, not failed) is skipped, so a rerun writes nothing.
- **Time budget.** It starts no new subject-date once `QUANT_ANALYTICS_MINUTES` (default 8) are
  spent, and lists what it skipped (`skipped_for_time`); the next run picks them up. The Bicep
  `replicaTimeout` is 600 seconds, so a run started at 09:30 is stopped by 09:40 at the latest.
- **Quality.** After writing it runs the table's own contract rules (from
  `publish_gate_rules.json`, generated from the contract) on the dates it built and reports each
  rule's count. A rule of severity error that finds rows makes the exit 1. These checks run in this
  job only: the job is **not** in `certified_build.GATE_JOBS`, so a failure here never holds the
  certified build for other pages.
- **Output.** One JSON line per subject-date it rebuilt (each section's status and build time),
  then a summary: status (`success`, `budget_spent`, `blocked`, `failed`, `held_back`), counts,
  `seconds_per_subject_date`, `skipped_for_time`, `blocked`, `held_back`, `failed` and
  `quality_rules`. Exit 0, 1 (a failure, a blocked date or a broken rule) or 65 (held back).
- **Lineage.** With `LINEAGE_RECORDING=true` the run is recorded under its job name with its
  inputs, and every row it writes carries the run id.

### Run time

Measured by `tests/test_quant_snapshots_db.py` on the local test server (load, build, write and
lock, for one subject and date):

| Subject | Seconds |
|---|---|
| 60 holdings, three years of daily data, a trade every other day | 2.1 |
| 8 holdings, 560 days | 0.5 |

The expensive sections, for the 60-holding subject (milliseconds): the 2000-sample bootstrap
(`skill`) 120; DCC (`correlation`) 87; EVT and its backtest (`tail`) 193; the regime model
(`regime`) 801; `holdings_risk` 258. Without a database the nine sections build in about 2
seconds (`tests/test_quant_snapshots.py`, `PerformanceTests`). At these times one run covers well
over a hundred subject-dates inside the 8 minutes; the real figure is measured on the restored
copy on Fri 30 Oct, and if it cannot fit, the schedule moves to 10:00 (Medallion review, note 3).

## Settings

| Setting | Where | Values | Default |
|---|---|---|---|
| `QUANT_ANALYTICS_BUILD` | job (Bicep `quantAnalyticsBuild`) | `off`, `on` (anything else is off) | `off` |
| `QUANT_ANALYTICS_MINUTES` | job | minutes the run may start new subject-dates in | `8` |
| `LINEAGE_RECORDING` | job (Bicep `lineageRecording`) | `true`, `false` | `true` in the Bicep |
| `PUBLISH_GATE_MODE` | job | `off`, `shadow`, `enforce` (`MEDALLION_ARCHITECTURE.md`, the publish gate) | unset (off) |
| `MEDALLION_DIR`, `IBKR_RUN_MIGRATIONS` | job | `/app/medallion`, `false` | as in the Bicep |

The website's own settings for the quant cards (`QUANT_ANALYTICS_PAGES`) and the Desk page
(`DESK_PAGE`) belong to those changes; they read this table and the Desk views.

## Grants and registration

The migration grants nothing itself. `bootstrap_postgres.py`, which runs it, gives every login the
same rights as on the comparable gold table (`risk_analytics_snapshots`), on the gold table and on
its public view: the job's app login SELECT, INSERT, UPDATE and DELETE (the schema-wide grants), the
website login SELECT, and the approval login SELECT on `as_of_date` only (its count and freshness
column, `layer_quality.COUNT_COLUMNS`). The Desk views get SELECT for the app and website logins, as
every silver view does.

Registered, held: `catalog.json` (the table and both views with `"held"`, and the job), the three
contracts, `layer_quality.VOLUMES` (`held=True`), `layer_freshness.RULES`
(`gold.quant_analytics_snapshots`, due 10:00 UTC, 4 days; `held=True`, so it is not checked at all
until the table exists, and **not required**, so an empty table never raises an incident while the
job is off), `publish_gate.TABLES` (lineage, holds back) and `JOB_TABLES["quant-analytics"]`, and
`lineage.TRACEABLE` and `JOB_INPUTS`. Checks on held objects wait for their migration, so the
administrator's checks and reports read as today until 076 runs.

## Switching on, in order

Each step is its own day's change. Stop at any step that does not end as described.

1. **The table (Wed 11 Nov), alone.**
   - Move `infra/medallion/drafts/quant_analytics.sql` to
     `migrations/postgres/076_quant_analytics.sql`, and in the same change drop `"held"` from the
     catalog entry `public.quant_analytics_snapshots` (`test_medallion_catalog` asks for this).
     The undo stays in drafts.
   - Take the snapshot and run the bootstrap job once, as `MEDALLION_S3_RUNBOOK.md` steps 2 and 3
     do, with `--only-migration 076_quant_analytics.sql` and `--allow-pending` naming every held
     file still pending (077 among them, if it is in `migrations/postgres` by then). It ends with
     `Migrations applied: 076_quant_analytics.sql.` A second run applies nothing.
   - Check as the runbook's step 4 does. The table is empty; the job is still not deployed.
2. **Deploy the job with the build off.** Deploy `infra/staging-quant-analytics-job.bicep` with
   `quantAnalyticsBuild=off` (the default). Its 09:30 run prints `{"status": "off", ...}` and
   exits 0, and nothing is written.
3. **Run it once by hand, build on, for one subject.** Start one execution of
   `ibkrv2stg-quant-analytics` with `QUANT_ANALYTICS_BUILD=on` and the arguments
   `scripts/build_quant_analytics.py --subject <one pilot account>` (an override for that
   execution only), outside 09:05 to 09:50.
4. **Check, then set it on.**
   - The summary: `status` `success`, every entry in `quality_rules` passed, and
     `seconds_per_subject_date` well inside the budget.
   - The rows: nine for the subject and date, none `fail`, and each unavailable section with a
     reason that is expected (`SELECT section, quality_status, payload_json->>'available',
     payload_json->>'reason' FROM gold.quant_analytics_snapshots WHERE subject_id = ... AND
     as_of_date = ...`).
   - The publish gate's read of it: `python -m ibkr_etl.publish_gate quant-analytics --since
     <today>` reports no "could not" reasons.
   - Then redeploy the Bicep with `quantAnalyticsBuild=on`. The next 09:30 run builds every
     subject's latest date. Older dates can be filled in by hand with `--since`, a few weeks at a
     time, inside the budget.
   - Once it has run for a week, the freshness rule can be made required (`layer_freshness.RULES`).
5. **The Desk views (077), on their own day.** Move `desk_live_views.sql` to
   `migrations/postgres/077_desk_live_views.sql`, drop `"held"` from its two catalog entries, and
   run it alone the same way (`--only-migration 077_desk_live_views.sql`). It needs nothing from
   076 and changes nothing existing: `agent.live_executions` and the other live views keep reading
   what they read today.

## Undoing

- Lighter: redeploy with `quantAnalyticsBuild=off`. The rows stay; the website's cards are turned
  off by their own setting.
- Whole: `quant_analytics_undo.sql` drops the table and its view (with their index, policy,
  trigger and grants), and nothing else; `desk_live_views_undo.sql` drops the two Desk views and
  nothing else. Run each as its own numbered migration with `--only-migration`, or by psql. Keep a
  copy first if the payloads may be wanted (`pg_dump -t gold.quant_analytics_snapshots`), and
  delete the 076 or 077 ledger row by hand if it should run again later. The tests apply and undo
  the two drafts in either order and find nothing left.

## Not in v1, and why

These need data that is not in silver yet (phase 3). Each needs a bronze capture and a silver
table, so each is its own change with Medallion:

- **Liquidity:** daily traded volumes.
- **Macro exposures:** macro series.
- **Factor attribution:** factor returns.
- **Crowding from 13F filings:** the filings.
- **Fair value:** dual-listing pairs.
- **Spread paid:** quotes at the time of each fill.

Also left out of v1: the Alpha World MS and T and the profiled composites (their own HKD
pipelines), and the trade sections for derived subjects (no trades are stored for them).

## Tests

- `tests/test_quant_snapshots.py`, no database: every builder on synthetic rows, with known
  answers (equal weights give a Herfindahl of 1/n, ex-ante contributions add up to the total,
  currency effects add up to the excess return, a hand-worked order's shortfall); the unavailable
  paths; fingerprint stability; the setting off doing nothing; JSON-ready payloads with finite
  numbers; and the 60-holding, three-year build time.
- `tests/test_quant_snapshots_db.py`, PostgreSQL (`AGENT_TEST_PG_ADMIN_DSN`): each held draft
  applied alone with bootstrap as its day will run it; the logins' grants and row security; the
  Desk views' rows; the loader reading back exactly what was written; the job's full build of two
  synthetic accounts, each payload equal to the pure builders' output; an unchanged rerun writing
  nothing and a one-input change rebuilding only its sections; a failing day held back whole by
  the publish gate; the reader's fallback; the time budget and the lock; lineage; and both undos in
  either order leaving no trace.
