# Portfolio system and Azure resource group review

Review date: 6 October 2026. Live observations were collected approximately
11:50 to 12:00 UTC from `rg-ibkr-database-v11`.

The owner subsequently approved implementation of all improvements. Later
changes and acceptance evidence are tracked in
[the implementation record](SYSTEM_IMPROVEMENTS_2026-10-06.md). This review
preserves the original read-only baseline, not the subsequent deployed state.

The immediate priority is analytics correctness, not additional AI deployment.
Three scheduled jobs failed today, and the website reports failed analyst
assurance for China & Hong Kong and U16088606-Greater China. The cancelled
agentic rollout also retains running resources and notifications whose website
inbox has been retired.

This is a read-only review of the live resource inventory, runtime settings,
scheduled execution history, selected redacted logs, database metrics, access
assignments, billing data, website health page and relevant application code.
Only this local review note was added. No cloud configuration, permission,
schedule, IBKR record, analytics result, report or approval was changed.

## Current operating baseline

| Item | Observed state |
| --- | --- |
| Resource inventory | 136 resources, including 64 jobs, 20 user-assigned identities, three container apps and three PostgreSQL servers |
| Job triggers | 16 scheduled jobs and 48 manual jobs; scheduled jobs retain zero immediate retries |
| Website | Current ready revision `ibkrv2stg-web--0000113`; one minimum and two maximum replicas; 1 CPU and 2 GiB per replica |
| Active website authorization | Database mode |
| IBKR archives | Both v1.1 `raw-statements` and v2 `ibkr-raw` have legal holds and no anonymous access |
| Storage recovery settings | Both accounts have versioning and 30-day blob/container soft deletion |
| Storage authentication | Shared-key and anonymous blob access disabled; TLS 1.2 required |
| Production databases | Both private, Ready, PostgreSQL 16, B1ms, 32 GiB, 14-day backups; high availability and geo-redundant backup disabled |
| Key Vaults | Three vaults with RBAC, purge protection and 90-day soft deletion; public network access enabled |
| Diagnostics | PostgreSQL metrics exported; SQL text and query log categories disabled |
| Monitoring | 18 enabled metric alerts and seven enabled scheduled-query alerts |
| Resource tags | Ten inventory records have no tags; tagging does not yet constitute a complete ownership and retirement register |
| Access assignments | 86 assignments within the resource-group/subscription inheritance scope |

These controls are useful foundations, but neither successful jobs nor a ready
website revision establish financial correctness or complete production
acceptance.

## Immediate analytics defects

### Exchange holiday rules disagree

The 6 October benchmark execution at 08:30 UTC failed with:
`SHCOMP comparison failed data-quality checks: freshness`.

The derived execution at 08:50 UTC reported the same failure during analytics
for `hong-kong-china` and `alpha-world-ms`. This establishes a shared validation
failure, not two independent IBKR ingestion failures.

At 11:37 UTC the metadata monitor correctly classified the Shanghai observation
of 30 September as current for the scheduled cycle. The calculation code instead
compares common observations against a maximum age of four calendar days
(`src/ibkr_etl/benchmark_analytics.py`, lines 539 and 637 to 643).

Shanghai is officially closed from 1 to 7 October and reopens on 8 October.
The calendar-aware monitor and calendar-day calculation therefore disagree
about the same valid last market close. The official schedule is in the
[Shanghai Stock Exchange announcement](https://www.sse.com.cn/disclosure/announcement/general/c/c_20260915_10832273.shtml).

Use one approved market-calendar and publication-cutoff policy across producers,
comparisons, derived analytics, readiness gates and the website. Distinguish an
expected closure from genuinely missing observations. Do not simply increase
the tolerance globally or invent prices. Regression tests should cover long
holidays, different exchange calendars, delayed feeds and reopening sessions.
The current monitor supports 2026 calendars only; add explicit calendar expiry
warnings and reviewed next-year coverage.

### Required metrics are unavailable for two active portfolios

The 09:10 UTC analyst-assurance execution failed. Its recorded findings identify
`metrics.required`: one or more required metrics are unavailable or non-finite.
The live System Health page shows China & Hong Kong and Greater China failing,
while U16088571 passes for data dated 5 October.

Reconcile each missing metric and its consumed source versions after correcting
the calendar mismatch. The observed holiday defect does not independently prove
the cause of every missing metric. Rebuild only eligible unpublished analytics;
preserve original IBKR data and immutable published report snapshots. Acceptance
requires all affected account/date checks to pass with finite required metrics,
or an explicitly justified unavailable result.

The benchmark logs also contain Yahoo quote lookup failures for bare symbols
`700` and `66`. Review the instrument-to-provider mapping by stable instrument
identifier, exchange and currency. A lookup error alone does not prove all
historical prices for that instrument are absent. This mapping work can use the
existing provider without restarting the deferred Bloomberg project.

## Cancelled deployment resources need coordinated retirement

The following resources remain after the agentic rollout cancellation:

- `ibkrv2stg-agent-gateway`: external ingress, minimum one replica, 0.5 CPU and
  1 GiB; it remains continuously available.
- `ibkrv2stg-agent-report`: scheduled at 10:00 UTC, corresponding to 18:00 HKT;
  today's execution succeeded.
- `ibkr-v2-specialist-daily-report-ready` and
  `ibkr-v2-specialist-daily-report-missing`: both enabled.
- `ibkrv2stg-ownertrial-20261004`: a Ready private B1ms trial database server.
- Trial jobs, identities, access assignments, build images and protected evidence.

The owner-facing agent inbox and Codex setup UI are retired, so report
publication and notifications no longer have their original website destination.
Public ingress is exposure to review, not evidence of an authentication bypass.

Create an exact retirement register with dependencies, owner, retained evidence,
rollback requirements and disposition. Retire the report schedule and related
alerts together; review gateway availability, trial compute and access grants
before removing them. Do not delete protected archives, weaken IBKR legal holds,
revoke the owner's deliberately retained storage permissions, or disable normal
ingestion and existing portfolio administration merely because their names
contain automation terminology. No retirement action was performed in this review.

## Reliability and release improvements

### Replace timer spacing with validated dependencies

Today's failed benchmark and derived jobs were followed by successful risk and
month-end job executions. That does not prove those jobs published invalid
results; it demonstrates that process success is independent of upstream success.

Complete the existing deterministic workflow foundations with actual producer
evidence, account/source dependencies, transactional dispatch, leases and
idempotency. Require validated observations and financial readiness before
declaring downstream work current. Preserve Flex token-level coordination,
circuit breakers and zero immediate retries. Validate a complete shadow cycle
before replacing the authoritative schedules.

### Finish reproducible Azure releases

The current website uses an immutable image, and bounded builds and retained
release evidence exist. The v2 registry currently has no persisted ACR Task
definitions; this does not mean no ACR quick builds have occurred.

The programme record still distinguishes release-policy components and source
archives from a complete operational release service. Finish the selected
ACR-based process with repeatable source packages, actual dependency/container
scans, SBOM generation, isolated staging, authenticated evidence, owner approval,
database compatibility checks and verified rollback registration.

Different worker images are not automatically defects: some workers deliberately
have narrower runtimes. Maintain a release compatibility manifest for each
component, template and migration instead of forcing every job onto one image.
Do not package the dirty working tree wholesale.

### Validate risk snapshots at the read boundary

The background cache worker checks payload identity, structure, finite values,
payload hash and consumed-input fingerprints. The website risk endpoints
currently use `decoded_payload` and return a cache hit without applying those
same integrity checks. The main endpoint selects the stored hash but does not
verify it; the details endpoint does not select that hash.

Add a lightweight common read validator for the requested account/date,
analytics version, stored hash, structure and quality state. It should return
a clear unavailable or rebuilding state for corrupt/failed entries, without
performing heavy calculations in a request. This is a code-level protection gap,
not evidence that a currently stored payload has been tampered with.

Finish upstream correction propagation and historical invalidation separately.
A correct fingerprint of stale producer output does not prove the producer has
consumed a correction. Published reports must remain immutable.

## Capacity and disaster recovery

Between 29 September and 6 October, the v2 database averaged approximately
10.1 percent CPU and 62.5 percent memory; observed peaks were 85.0 and 87.7
percent. Storage remained near 14 percent, and CPU credits remained healthy.
The v1.1 server also retained healthy credits. There is no evidence of current
credit exhaustion causing today's analytics failures.

Both production servers nevertheless lack high availability. Define acceptable
recovery time and data-loss targets, then price a General Purpose and HA option
against those targets and measured concurrent load. Microsoft recommends
General Purpose for most production workloads and explains the limits of
[Burstable compute](https://learn.microsoft.com/en-us/azure/postgresql/compute-storage/concepts-compute).
Do not purchase more capacity solely to address a calendar-validation defect.

The execution record and retained evidence establish separate database and
57-file blob/version recovery drills. They do not establish a complete portal
failover. Finish an isolated application reconnect exercise, including sign-in,
account authorization, report downloads, job dependencies and measured RTO/RPO.
Adopt a recurring drill policy; never point a test restore over production.

The web connection helper opens a new PostgreSQL connection for each context
and has no explicit timeout in that helper. Evaluate bounded connection pooling,
connection/query deadlines and backpressure under load, with tenant settings and
read-only state reset correctly between pooled users. Establish endpoint latency
and error-rate targets before considering extra website replicas.

## Security and operational visibility

Both storage accounts allow their public endpoints from all networks and have
zero private endpoint connections. All three Key Vaults and both registries
also retain public network access. Authentication controls remain in place;
public endpoints do not mean anonymous access is permitted.

Map required egress, worker identities, DNS and build access before introducing
private endpoints or restrictive firewalls. Stage-test ingestion, secret access,
rendering, downloads and builds before a cutover. Microsoft's guidance describes
[private endpoints and restricted storage firewalls](https://learn.microsoft.com/en-us/azure/storage/common/secure-storage).

Review the 86 relevant role assignments against current duties. Remove obsolete
trial authority only through a reviewed retirement change. No observed service
identity held an RG Owner or Contributor assignment in the filtered inventory;
broad human access still warrants periodic review. Tenant MFA and Conditional
Access remain an explicitly deferred review, not a newly verified control.

The metadata monitor is still shadow-only and declares that absolute NAV, FX and
risk-free freshness is unverified. Its `no_findings_in_checked_scope` result must
not become an overall financial-health badge. Add reviewed calendar-aware
freshness, backlog age and quality alerts, with account, reason, last good date
and an actionable next step. Keep slow-query telemetry redacted rather than
enabling raw SQL logging indiscriminately.

The System Health page correctly reports failure, but it shows terse codes,
internal subject identifiers and failure counts rather than the underlying
actionable reasons. Its section labelled platform acceptance burn-in currently
covers only U16088571. Relabel pilot-only acceptance or expand it explicitly to
the other active portfolios. Add a timestamp and automatic status updates with
clear running, blocked, failed and completed states.

## Maintainability and financial acceptance

`web.py` contains approximately 9,857 lines, mixing routes, SQL, HTML and browser
logic. The upgrade chronology exceeds 8,700 lines, and cancelled trial tests
include fixed source-count/hash graphs such as a required 847-file inventory.
These evidence checks may protect a historical trial, but coupling ordinary
development to them makes routine changes unnecessarily difficult.

Extract bounded route/service modules gradually, centralize typed account and
report contracts, and separate frozen historical evidence checks from current
application regression tests. Preserve historical evidence rather than loosening
its checks. Publish a short current-status index linking to the detailed archive.

Complete independent month-end NAV, return, cash, FX and derived-portfolio
reconciliation, together with approved-template pixel, geometry, font and chart
tests. Parsing a PDF/PPTX or passing a large code suite is insufficient. Keep
Alpha World MS paused until its design-authority and composite-analytics
acceptance is signed off. Maintain account/month/version/hash checks on every
report download and preserve owner approval and revocation semantics.

## Cost management

Azure Cost Management returned approximately KRW 23,340 in month-to-date pre-tax
charges at the time of this review. PostgreSQL accounted for approximately
KRW 12,505, container apps KRW 4,770 and registries KRW 3,807. Billing can lag;
these are reported month-to-date costs, not a full-month forecast or a savings
estimate.

Prioritize the retained trial server and continuously available gateway for
cost attribution. Manual job definitions do not incur the same continuous
compute cost as a running app or database. Add consistent owner, environment,
purpose, cost-centre and review/expiry tags and resource-level budget reporting.
Retain verified rollback images and protected evidence; review other disposable
images and scratch copies only after identifying their recovery purpose.

Both Log Analytics workspaces retain 30 days of logs and have no daily ingestion
cap. Start with table-level usage and growth alerts, redaction and retention
policy. A low cap could blind failure detection; Microsoft cautions against
using [daily caps as the primary cost-reduction mechanism](https://learn.microsoft.com/en-us/azure/azure-monitor/logs/daily-cap).

## Recommended order

1. Correct the shared holiday rule, investigate the two assurance failures and
   verify the affected calculations without modifying IBKR source data.
2. Retire cancelled-rollout schedules, alerts, compute and obsolete access as one
   dependency-aware change, retaining evidence and normal portfolio services.
3. Complete dependency-driven readiness, correction propagation and risk read
   validation, with fault and stale-input tests.
4. Finish the reproducible Azure release process and full application recovery
   rehearsal.
5. Complete financial and visual acceptance before additional onboarding.
6. Stage network hardening and role review, then address measured performance,
   modularity, monitoring and cost improvements.

This review does not certify every financial figure, every historical report,
tenant security policy or maximum-user load. It identifies concrete current
defects and bounded follow-up acceptance work without restarting the cancelled
agentic deployment or the deferred Bloomberg project.
