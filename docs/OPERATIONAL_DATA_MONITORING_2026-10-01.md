# Operational data and workflow monitoring — verified diagnostic baseline

## Status

This extends approved upgrade 6. A read-only live baseline and tested monitoring
policy are complete. **These new database checks are not yet a scheduled Azure
monitor or email alert.** The three previously deployed schedule watchdogs
remain separate and enabled. No production database, app image, job settings,
financial formula, approval, or account activation was changed in this step.

## Live evidence

Corrected probe: `ibkrv2stg-bootstrap-ghxxzaw`, Succeeded. Captured
**1 October 2026 18:10:38 UTC / 2 October 2026 02:10:38 HKT**.
Probe payload SHA-256:
`70cd61e0702978bc1f6eccb0dcb62dc25b1c1b7dc201c0f36ac75abebff7ffb2`.

The database confirmed `transaction_read_only=on`. Only dates, statuses, IDs
and counts were emitted; no balances, holdings, credentials, personal identity
details, raw IBKR documents, queue payloads or error-message bodies were logged.

| Active subject | Latest NAV | Latest usable risk snapshot | Quality |
| --- | --- | --- | --- |
| U16088571 | 2026-09-30 | 2026-09-30 | warn |
| U16088606 | 2026-09-30 | 2026-09-30 | warn |
| hong-kong-china (China & Hong Kong) | 2026-09-30 | 2026-09-30 | warn |

Every currently mapped benchmark and comparison series had a usable observation
through 2026-09-30. USD UST-PAR-3M risk-free data had a `pass` observation for
2026-09-30. The institutional work table and outbox were empty, including failed
and dead-letter states. This is not a claim that legacy timer-driven jobs have
no failures; they do not all use these queues yet.

Alpha World MS remains in onboarding, not active. Its data were inventoried,
but it was excluded from active-account risk-coverage findings. Nothing here
resumes its onboarding or grants financial/visual acceptance.

The initial probe `ibkrv2stg-bootstrap-05ittj6` also succeeded, but the initial
pass-only policy incorrectly identified existing warning-quality analytics as
missing. The corrected probe/policy preserves separate pass and usable dates.
Current risk code deliberately returns `warn` when it has comparison rows;
provisional Yahoo prices/comparisons also carry warnings. No source quality was
upgraded or warning removed. This correction prevents a false missing-data
alert; it is not a numerical validation of the calculations.

Local evidence is in `.work/operational-observability-20261001/` and must remain
private/untracked. The corrected execution JSON contains the full allow-listed
inventory and local assessment. Earlier evidence is retained as superseded.

## Implemented safeguards

Verification: **470 unit tests passed**, including 22 new operational-monitor
and probe contract tests. The limited repository policy check scanned 351
source files and 53 contiguous migrations with no prohibited paths or matching
secret patterns. This is not a full vulnerability scan or financial certification.

- Dedicated metadata probe uses a repeatable-read, read-only transaction,
  explicit tenant filters and a 60-second per-statement timeout.
- One-off launcher retains the existing bootstrap image, managed identity and
  database secret reference; it never retrieves the secret value. It refuses
  a different subscription, unexpected template shape or nonzero retry limit.
- The existing maintenance credential is used only for this one-off diagnostic;
  a recurring monitor must use a properly scoped reader, not this credential.
- Evidence is split into indexed rows to avoid large single log messages.
  Missing rows, conflicting duplicates, missing completion markers or a
  truncated inventory cannot produce a healthy conclusion.
- Queue policy distinguishes runnable overdue work from future availability,
  scheduled backoff, unfinished dependencies and newly completed predecessors.
- Missing/expired leases, failed prerequisites and dead letters are distinct
  diagnostic states. Paused/non-active subjects are not treated as active
  account backlog. These states never authorize a retry or circuit reset.
- Risk coverage distinguishes missing usable snapshots, older snapshots,
  future dates and aligned dates with warnings. Expected calculator versions
  are checked by regression tests.

## What these checks do not establish

1. Absolute market freshness: an approved exchange/source calendar and
   publication deadline must establish the expected observation date. Equal
   NAV and risk dates could both be stale. Weekend-only logic is insufficient.
2. Source-value correctness, corrected-data propagation or report acceptance.
   Published reporting snapshots are not changed by this probe.
3. Full workflow readiness: capability-level pauses, source-level suspension,
   maintenance windows, Flex cooldown and the legacy/durable ownership boundary
   must be integrated before automatic paging. Current results are diagnostic.
4. Exact outbox dead-letter age: that table has no transition timestamp. The
   original creation time must not be presented as time spent in dead letter.
5. Notification delivery or a periodic check. A successful one-off probe is not
   an automated monitor. No new monitor service/image was deployed here.

## Next controlled implementation

1. Define versioned market/source calendars and publication deadlines for NAV,
   benchmark, FX and Treasury sources. Preserve explicit unknown coverage.
2. Add a least-privilege metadata-reader path and a renderer-independent
   monitoring deployment. Do not schedule the maintenance/admin probe.
3. Persist monitor observations and incident transition times, including
   dead-letter entry, acknowledgement, maintenance and recovery. Avoid paging
   on temporary lag while a legitimate daily pipeline is still running.
4. Add worker/source/capability scope and cooldown semantics, then evaluate in
   shadow mode through a complete daily cycle before enabling email alerts.
5. Test missing monitor heartbeat and an agreed end-to-end fire/resolution
   notification. Never trigger a live IBKR retry merely to test an alert.

Original ingestion remains 16:00 HKT, with zero immediate retries. The other
approved upgrade tracks and Alpha World acceptance remain unfinished.

## Subsequent continuation: exchange calendars and restricted reader rehearsal

Implemented `market_calendar.py` and 16 regression tests. The versioned 2026
catalog covers NYSE, Nasdaq, Hong Kong, Shanghai and Taiwan trading sessions,
including holidays and half-days. Unsupported years/markets produce an explicit
unverified result; the code never substitutes Monday-Friday for missing coverage.
Half-days remain trading sessions. Exceptional unscheduled closures still need
an override/review mechanism; no claim of real-time calendar completeness is made.

Primary calendar sources inspected on 1 October 2026:

- [NYSE calendar](https://www.nyse.com/trade/hours-calendars)
- [Nasdaq calendar](https://www.nasdaqtrader.com/trader.aspx?id=Calendar)
- [HKEX 2026 securities calendar](https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2025/ce_SEHK_CT_075_2025.pdf)
- [Shanghai 2026 exchange announcement](https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml)
- [TWSE official 2026 JSON](https://www.twse.com.tw/rwd/en/holidaySchedule/holidaySchedule?response=json&queryYear=2026)

TWSE JSON was retrieved directly from the official service. Its clearing-only
12/13 February dates are correctly treated as non-trading dates. Shanghai's
1–7 October closure is not mistaken for a four-day stale-data failure; Hong Kong
and Taiwan use their own calendars. No statement/financial data were sent to
these websites.

The shadow monitor mirrors the existing benchmark producer, which runs at
16:30 HKT and caps *all* markets at its US 18:30 reporting cut-off. Its deadline
includes the configured 20-minute maximum runtime and a proposed 15-minute
monitoring grace, giving 17:05 HKT. This is an internal operational policy, not
a Yahoo service-level guarantee. No producer schedule or price-fetch logic was
changed. A future schedule/cut-off change must update this policy as well.

Applied the new policy to the previously captured successful live evidence
`ibkrv2stg-bootstrap-ghxxzaw`: all seven active subject/benchmark mappings are
`current_for_scheduled_cycle`, with source `warn` status preserved. This assesses
the **18:10 UTC captured snapshot**, not a newly fetched later snapshot. Alpha
World's mappings remain excluded because the account is not active. NAV, FX and
Treasury absolute publication deadlines remain unverified. The monitor remains
in shadow/local assessment mode, not scheduled/paging mode.

### Least-privilege permission integration test

Added explicit column-level grants in `metadata_monitor_access.py`. The proposed
reader has no balance/price/rate values, report/queue payloads or credential
references. PostgreSQL column privileges were checked with the
[GRANT documentation](https://www.postgresql.org/docs/current/sql-grant.html).

First transactional rehearsal `ibkrv2stg-bootstrap-tx8k73e` failed safely because
the security-invoker `subject_nav_series` view requires privileges on its
underlying financial tables. The transaction rolled back. The collector was
changed to query only date and identity columns on those base tables; it was
not given balance-column access. This diagnostic execution may produce a
failure email; it is not a Flex request failure or an application outage.

Corrected rehearsal **`ibkrv2stg-bootstrap-rmnzo9d` succeeded**. Payload SHA-256:
`7df5239bbecc3136bff07b034703a4de8529ebb45998d6f469eb4c071f3d9853`.
Verified:

- All 12 metadata sections collected under the restricted trial role.
- Seven actual `SELECT ... LIMIT 0` attempts on sensitive columns denied.
- Zero public-schema table INSERT/UPDATE/DELETE/TRUNCATE privileges.
- Zero callable public-schema SECURITY DEFINER functions.
- Trial role and membership/grants fully rolled back, confirmed afterward.

No persistent role, password, identity assignment or scheduled monitor was
created. The trial used the existing maintenance identity only to create and
roll back the test role; metadata collection itself used the restricted role.
Before deployment, a dedicated login/managed-identity binding and explicit
tenant authentication boundary are still required. Existing application users
and roles were not altered. Do not schedule this maintenance-credential trial.

Suite after this continuation: **488 tests passed**, including 18 new calendar
and grant-policy tests. Limited repository policy scan: 358 source files,
53 contiguous migrations, no prohibited paths or matching secret patterns
(before this documentation-only update). Existing pypdf deprecation and
simulated failure-test messages remain; these are not new live IBKR calls.

Next: deploy an isolated metadata reader and monitor after tenant binding,
complete NAV/FX/Treasury publication policies, retain observations, observe a
full daily cycle in shadow mode, and then enable persistent-incident alerts.

## Dedicated shadow monitor deployed — later continuation, 1 October

Implemented the next stage above; previous no-deployment statements describe
earlier checkpoints, not current status.

### Scope and access

- Job: `ibkrv2stg-metadata-monitor`, East Asia, existing private-network Container
  Apps environment. Minutes 07 and 37 every hour (UTC and HKT have the same minute
  values), zero retries, 180-second maximum runtime, 0.25 CPU and 0.5 GiB.
- Azure identity: `ibkrv2stg-metadata-monitor-identity`. Only AcrPull at the
  existing registry and Key Vault Secrets User at the **single secret**
  `database-metadata-monitor-url`. No Flex, storage-write, or ARM administration
  grant was added to that identity. No database administrator secret is provided.
- PostgreSQL login: `ibkr_metadata_monitor`, no role memberships, no superuser,
  role/database creation, replication or RLS-bypass attributes. Connection limit
  two, default read-only transactions, 60-second statement and idle-transaction
  limits. Only the reviewed metadata column grants were added.
- The monitor explicitly starts a repeatable-read, read-only transaction and
  checks sensitive-column denials, application-table write privileges and
  callable public SECURITY DEFINER functions every run. Permission drift fails
  the run closed; exception output contains only the exception class.
- This is a trusted **system-wide operator metadata identity**, not a user
  identity or a new multi-tenant security boundary. Its fixed job queries tenant
  `00000000-0000-0000-0000-000000000001`; tenant configuration is not accepted from
  browser input. Its login must not be reused by website users or the AI agent.
- Password was generated in process memory and stored directly in Key Vault.
  No password was printed, entered in chat, placed in source or command arguments.
  Existing application credentials and user permissions were not altered.

The payload uses the existing immutable maintenance image
`sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c`
and a source-hashed Python command bundle. No new production image was built;
no proprietary document renderer is invoked. Moving this small monitor into
a dedicated minimal image remains a later release-pipeline improvement.

### Evidence and release gates

- Provisioning execution `ibkrv2stg-metadata-provision-hd3gzzk`: **Succeeded**;
  12 sections collected under the real login, seven denied columns, no
  application writes. This was an additive real login creation, unlike the
  earlier rolled-back trial.
- Monitor execution `ibkrv2stg-metadata-monitor-plylkui`: **Succeeded**;
  capture `2026-10-01T18:41:34.044115+00:00`, four subject-risk records (one
  non-active) and seven active benchmark checks. Eleven detail records and one
  completion record were verified. `inventory_complete=true`, access checks
  passed, `no_findings_in_checked_scope`. This does not mean all system data or
  financial calculations were independently reconciled.
- Evidence fingerprint:
  `ebf1bc14b1ac84b9159493b66462c6611dcdd98ed79ae09ea57ba401d8637d1c`.
- Azure records execution secret references using internal `cappjob-*` aliases
  and normalizes resource-ID capitalization. Release validation now handles
  those representations while still matching exact image, command, code and
  literal environment values, plus the real current Key Vault/identity bindings.
  A failed execution, changed payload, unexpected secret binding, missing log
  records or conflicting duplicates blocks scheduling.
- Source/tests: `deploy_metadata_monitor.py`, `provision_metadata_monitor.py`,
  `run_metadata_shadow_monitor.py`, `test_metadata_shadow_deployment.py`.
- Private evidence is retained under `.work/operational-observability-20261001/`;
  metadata-only structured `METADATA_MONITOR` records are retained in the
  existing Log Analytics workspace according to its configured retention.
- Full suite: **498 tests passed**. Limited source policy check: 362 source
  files, 53 contiguous migrations, no matched secret patterns/prohibited paths.
  This is not a container-vulnerability certification or visual acceptance.

### Operational limits and follow-up

This is **scheduled shadow monitoring**, not automatic remediation or new
data-quality email alerts. The manual live test passed; a scheduled execution
and a complete daily producer cycle have not yet been observed in this turn.
Do not promote it to incident paging until those observations, maintenance/
capability pause semantics, source cooldown semantics, persistence thresholds
and the monitor's own missed-run alert have been validated. The original
13 schedule watchdog targets remain unchanged and do not yet include this
new shadow job. NAV, FX and Treasury absolute publication freshness remain
unverified; exchange calendar coverage stops at 31 December 2026.

The original 16:00 HKT ingestion, Flex circuit breakers and report approvals
remain unchanged. Alpha World MS remains non-active; no onboarding activation,
report regeneration, approval or publication occurred.

Rollback: switch **only** `ibkrv2stg-metadata-monitor` to a Manual trigger and
stop any running execution if necessary. This stops polling without changing
production data. The dedicated login can then be set NOLOGIN if required;
do not rotate/delete application credentials or drop dependent objects.
The new temporary provisioning job is removed after verification; its source,
sanitized evidence and Azure logs are retained. It can be recreated deliberately
from source, but its script refuses to overwrite an existing database login.

### Follow-up: watchdog for the monitor itself

Deployed `ibkr-v2-metadata-monitor-watchdog` through
`metadata-monitor-watchdog-20261001`, reusing the reviewed native query-alert
infrastructure. The alert requires a `METADATA_MONITOR` completion record with
`kind=complete`, `mode=shadow` and `access_check=passed`. It does not treat a
container start, partial log or failed access check as a successful monitor run.

The deadline is 75 minutes: two 30-minute opportunities and 15 minutes for log
delivery. Evaluation is every 15 minutes, severity 2, auto-mitigation enabled,
with the existing operations action group. A successful manual check can also
establish monitor liveness; this is not proof the cron dispatcher worked. Missing
log delivery is deliberately an investigation signal, not proof of stale data.

Live pre-deployment query found zero missing completions. A synthetic empty
stream produced exactly one missing-monitor finding. Deployment succeeded and
query, enabled state and action-group readback matched. No test notification
was sent. Financial findings still remain in shadow mode, and full daily-cycle
and maintenance-pause acceptance remain pending. Intentional suspension of the
monitor must also disable this one watchdog to avoid an expected absence alert.

Implementation: `scripts/verify_metadata_monitor_watchdog.py` and three regression
tests. Private query parameters/evidence are in the operational evidence folder.
Rollback: disable **only** `ibkr-v2-metadata-monitor-watchdog`; this does not change
the monitor, other watchdogs, ingestion or application permissions.

### First scheduled execution verified

`ibkrv2stg-metadata-monitor-29848027` started automatically on 1 October 2026 at
19:07 UTC and succeeded. The deployment verifier accepted 12 expected records,
including restricted-access evidence and the final completion record. Saved
evidence is `.work/operational-observability-20261001/ibkrv2stg-metadata-monitor-29848027.json`.
Unlike the earlier manual run, this establishes one successful cron dispatch.
It does not establish a complete daily ingestion-to-report cycle or test email
delivery. Data-quality findings remain shadow-only; no remediation was enabled.
