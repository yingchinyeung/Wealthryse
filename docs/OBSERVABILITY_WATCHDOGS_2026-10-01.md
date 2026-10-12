# Schedule observability upgrade — 1 October 2026

This continues approved upgrade 6 independently of the pending presentation
runtime permission review. No new application/container image was built.

## Azure monitoring added

Incremental deployment `schedule-watchdogs-20261001-r2` succeeded. Its reviewed
what-if created only these three scheduled-query rules:

| Rule | Coverage |
| --- | --- |
| `ibkr-v11-daily-schedule-watchdog` | Original 16:00 HKT IBKR ingestion |
| `ibkr-v2-daily-schedule-watchdog` | Ten daily v2 jobs: account sync, benchmarks, sectors, derived portfolios, risk cache, monthly-package checking, analyst assurance, burn-in monitoring, institutional dispatch, risk-free rates |
| `ibkr-v2-background-heartbeat-watchdog` | Five-minute administrator and Flex-discovery workers |

Rules are enabled, severity 2, stateful/auto-mitigating, evaluated every 15
minutes, and use the existing `ibkr-v11-daily-alerts` action group. No recipient
was added. Azure Monitor query-alert costs apply; no new compute tier or VM was
introduced. The rules read existing system lifecycle logs, not console messages,
SQL text, holdings, credentials or statements.

## Detection semantics

- Daily jobs: select the most recent scheduled UTC slot that is at least 15
  minutes old. Match the exact scheduled execution name, not a later manual run.
  Absence of start AND terminal evidence produces a missing-start signal.
- A start without terminal evidence produces a signal only after that job's
  configured maximum runtime plus the 15-minute logging grace.
- `Completed`, `BackoffLimitExceeded` and `DeadlineExceeded` count as terminal
  telemetry. Failure is NOT classified as success; separate existing failed-job
  alerts handle it, avoiding duplicate absence alerts for an observed failure.
- Background workers: no scheduled start within 20 minutes, or no terminal
  telemetry within 35 minutes, produces a heartbeat signal. This is liveness
  monitoring, not proof that every individual five-minute slot ran.
- Missing log rows still produce a signal through a left join against the
  expected job inventory. Unrelated secret-sync/platform events cannot satisfy
  a job heartbeat. Missing tables/query errors are not silently ignored.

An alert means **expected execution telemetry is absent**, which may be a job
problem or delayed/broken log delivery. It does not prove that ingestion failed,
that portfolio data is stale, or that a job which completed performed useful
work. No alert automatically retries or starts a job, resets a circuit breaker,
changes formulas, approves reports or changes access.

## Verification

- All **448 local tests passed**, including 10 schedule-monitor regression tests.
- Limited repository policy scan passed (343 source files, 53 migrations).
- Read-only live queries initially returned **zero issues** for all three rules.
- Empty-stream synthetic tests detected **1 + 10 + 2 = 13 missing jobs**. These
  simulations queried Log Analytics; they did not inject logs, fire alerts,
  send test emails or interrupt production jobs.
- Checked live cron expressions and maximum runtimes against every daily rule;
  checked both five-minute cron expressions.
- Verified the numeric scheduled-execution suffix convention against actual
  Azure system events for the target jobs. It is a platform convention rather
  than a guarantee; revisit the matcher if Azure changes execution naming.
- Local evidence and generated deployment parameters are under
  `.work/observability-20261001/`. `verify_schedule_watchdogs.py --verify-deployed`
  compares deployed queries, enabled states and action-group destination and
  reruns the live/synthetic checks without starting any job.

Initial deployment `schedule-watchdogs-20261001` was rejected before creating
any rules: Azure requires one evaluation period when an absence query has no
event `TimeGenerated` output. The corrected configuration uses one period and
the explicit query grace windows. It does NOT claim two-consecutive-evaluation
debouncing. No artificial timestamp was added to pretend missing events exist.

## Operations / safe changes

1. If an email fires, identify its `JobName` and inspect Azure job execution
   history for the expected slot, then check Log Analytics delivery.
2. If execution failed, follow the existing failure/circuit-breaker procedure.
   Do not repeatedly run IBKR requests to clear an absence alert.
3. If intentionally pausing a monitored job or changing its schedule/runtime,
   update its watchdog specification in the same controlled change. The current
   rule cannot infer an intentional pause from absent logs.
4. Run `verify_schedule_watchdogs.py --enable` to check live schedules and
   generate reviewed parameters. It refuses enable-ready parameters when its
   live queries already return issues; inspect those issues first.
5. Rollback is to redeploy `infra/platform-schedule-watchdogs.bicep` with the same
   reviewed rule definitions and `enabled=false`. This disables only these new
   rules; never pause a production job merely to silence its alert.

## Remaining observability work

### 2 October addition: monthly execution watchdog deployed

`ibkr-v2-monthly-autonomy-schedule-watchdog` is now enabled, using the existing
action group and 15-minute evaluation cadence. It monitors the scheduled
`ibkrv2stg-autonomy-review` execution (`0 1 1 * *` UTC), allowing 15 minutes for
log delivery and the configured runtime before declaring a missing terminal
event. A manual execution cannot satisfy the expected numeric scheduled ID.
Deployment verified the current schedule/retry setting before and after the
change. The rule has a two-day query lookback and checks only the first 48 hours
after the monthly due time. Outside that window it returns no issue: automatic
alert resolution at the window boundary does **not** demonstrate recovery.
Durable month-level incident tracking remains outstanding. Existing execution
failure alerts handle failed terminals; this rule does not certify useful work,
financial freshness or delivery of an email to a recipient.

The live query returned no missing event. Empty-stream synthetic evaluation
detected the missing monthly execution at its deadline; evaluation outside the
window returned zero. ARM what-if permitted only creation of the named rule.
Rollback: disable only `ibkr-v2-monthly-autonomy-schedule-watchdog`; do not pause
or rerun the production job to silence the alert. Implementation and checks:
`monthly_schedule_monitor.py`, `test_monthly_schedule_monitor.py` and
`deploy_monthly_watchdog.py`. Private deployment/readback evidence is under
`.work/upgrade-deployments-20261002/`.

### Remaining beyond the deployed rules

- Calendar-aware NAV, FX, benchmark, risk-free and analytics freshness alerts
  based on actual validated observation dates (not execution exit codes).
- Dead-letter/overdue-lease age and workflow backlog alerts.
- Durable month-level state beyond the newly deployed bounded monthly watchdog.
  Manual-only and paused onboarding jobs are intentionally not covered by these
  daily/heartbeat rules; their existing execution-failure alerts remain.
- Log-query health monitoring, maintenance-window handling and long-term
  scheduled-event convention checks.
- Reviewed/redacted diagnostic logs, slow-query thresholds and cost dashboards.
- An actual email delivery/fire-and-resolve drill and observation through the
  next daily cycle. Configuration and synthetic query tests are not proof of
  end-to-end notification delivery.

Original IBKR ingestion remains **16:00 HKT** with **zero immediate retries**.
No existing job, image, formula, identity, approval or template was modified.
Alpha World MS onboarding remains paused; release-pipeline permission review
remains pending. The other upgrades are not claimed complete by this change.
