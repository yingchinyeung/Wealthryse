# System and Azure resource-group audit — 1 October 2026

## Scope and safety boundaries

Reviewed local application, infrastructure and test sources; inventoried the
resource group `rg-ibkr-database-v11`; checked app/job configurations and recent
executions, Azure logs, PostgreSQL utilization, authentication, private database
networking, storage recovery, Key Vault protection, registry exposure, budgets,
alerts and the authenticated owner portal.

This is an operational/code review, not a penetration-test certification or a
full independent recalculation of every historical observation. No trades,
credential rotations, additional Flex calls, client-publication approvals or
owner report approvals were performed. Alpha World MS onboarding remains
paused. Existing financial formulas and scheduling times were not changed.

## Verified problems and corrections

1. **Wrong runtime image for reporting.** Recent web/report deployments used
   `Dockerfile.azure`, which lacks Node and approved bundled report assets. Azure
   recorded an onboard-render failure reporting a missing approved presentation.
   Corrected to the full Dockerfile. Added an offline, non-root build test that
   opens every bundled PDF/PPTX, imports the presentation API and rasterizes a
   native chart canvas. Missing assets now fail the build, before deployment.
2. **Unpinned Python dependency resolution.** Builds previously installed broad
   dependency ranges. Added a hash-checked requirements export from the existing
   uv lock and `--no-deps` project installation. An audit found PyJWT 2.14.0
   affected by CVE-2026-101918; updated the lock and test environment to 2.15.1.
   The follow-up pip-audit reported no known vulnerabilities in the checked
   requirements. This does not cover OS packages or the private Node dependency
   tree and does not guarantee absence of undisclosed vulnerabilities.
3. **Incomplete platform-health aggregation.** The UI counted physical accounts
   only and selected a single latest assurance result. Changed the count to the
   unified active-subject NAV view, listed every active non-master portfolio's
   latest assurance, and made any failure fail the aggregate. Missing, invalid,
   future-dated or older-than-previous-day assurance produces a warning.
4. **Authorization fallback could undo a denial.** A successful database lookup
   returning no authorized identity could fall back to old environment access.
   Database denials now return 403. Database-outage fallback is restricted to the
   exact configured owner; old colleague allow-lists cannot restore access.
   Database-only mode remains fail-closed for everyone.
5. **Missing job-failure alerts.** Added enabled alerts, using the existing action
   group, for derived daily, risk snapshots, institutional daily, Flex discovery,
   onboarding analytics, onboarding rendering and historical reports. The group
   now has 18 metric alerts. No job schedule was changed by the alert deployment.
6. **Stale operational wording and rollback target.** The Administration page
   falsely implied the institutional dispatcher was unscheduled; the Azure job
   is scheduled. Replaced that claim with schedule-management wording. The
   registered automatic rollback target was revision 0000041, substantially
   older than current access/report features. A dedicated baseline-registration
   tool now changes only the verified target, not autonomy permissions, review
   deadlines, kill switches or schedules. Final deployed target is recorded in
   the verification section below.

Regression tests cover missing/corrupt report assets, the required build gate,
hash-locked dependencies, cross-account health aggregation, stale/missing
assurance, ingestion failure and authoritative database denials/outage fallback.

## Infrastructure observations

| Area | Observed state | Assessment |
|---|---|---|
| Resource group | 79 resources after the seven new alerts | Shared legacy/v2 group; logical rather than subscription-level isolation |
| PostgreSQL, both servers | Version 16, B1ms, 32 GB, auto-grow, 14-day backups, private networking, public access disabled | Keep economical size; backups configured but restore drill still needed |
| v2 PostgreSQL utilization, previous 24h | Average CPU ~9.7%, peak ~81.8%; average memory ~61.2%, peak ~83.0%; storage ~13.9%; peak active connections 10 | No evidence for immediate scale-up; reassess with growth/load testing |
| Storage, both accounts | HTTPS, TLS 1.2, anonymous blobs disabled, shared keys disabled, blob versioning and 30-day blob/container soft delete | Good recovery baseline; authenticated public service endpoints still enabled |
| Key Vault, three vaults | RBAC, soft delete and purge protection; 90-day recovery retention | Protected; public service endpoints still enabled |
| v2 registry | Basic tier, admin login disabled, anonymous pull disabled | Authenticated public endpoint; immutable deployment digests preferred |
| Web | HTTPS-only, Entra single-tenant authentication, one warm replica, max two, 1 CPU/2 GiB | No compute increase applied |
| Credential broker | Internal ingress, separate identity, scale-to-zero | Preserve isolation; credential values not retrieved by this audit |
| Log workspaces | 30-day retention, no daily ingestion cap | Alert on cost/ingestion growth; avoid a hard cap silently disabling monitoring |
| PostgreSQL diagnostic export | No diagnostic settings found on v2 server | Add a reviewed, privacy-conscious diagnostic policy |
| Budget | KRW 120,000 resource-group monthly budget with actual/forecast email thresholds | Notification threshold, not a hard spending cap; Oct 1 zero spend is not a full-month estimate |
| Analyst NIC | Unattached, no public IP; associated NSG retained | No VM exists; retain until cleanup ownership is confirmed |

## Operational evidence before repairs

The latest execution of each inventoried job was successful at the time of the
inventory, including a successful rerun after earlier failures. This does not
mean every earlier run succeeded. On 1 October the original IBKR job ran
08:00–08:03 UTC (16:00–16:03 HKT). Account sync, benchmark, sector, derived and
risk-snapshot jobs then succeeded in their scheduled windows. No extra Flex
request was needed for this audit.

Logs showed the missing-template rendering failure and an older assurance
worker rejecting non-pilot accounts/layout versions. Later reruns succeeded.
Reporting and assurance producers/readers are included in the repaired image
rollout to reduce this version mismatch.

Yahoo messages about securities unavailable in 2008 are not by themselves
database corruption: crisis replay supports explicit proxies. Review proxy
coverage and provenance rather than assuming every provider warning is fatal.

## Remaining upgrades, in recommended order

Owner subsequently approved all eight upgrades. Implementation status and later
live evidence are tracked in [the approved-upgrades execution record](APPROVED_UPGRADES_EXECUTION_2026-10-01.md).
The audit findings below describe the original baseline, not completion of the
subsequent programme.

1. **Recovery exercise.** Restore PostgreSQL into a separately named private
   temporary server, verify schema/row counts/report manifests, measure RTO/RPO,
   then remove the temporary resource only after review. Configured backups are
   not proof of successful recovery. Do not overwrite production for a test.
2. **Dependency-driven workflow.** Replace timer-only spacing with durable,
   idempotent prerequisites: ingestion → reconciliation → benchmarks/FX →
   derived portfolios → risk → reports → assurance. A successful job with no
   new data must not imply all downstream data is current. Preserve Flex
   token-level circuit breakers and zero immediate retries.
3. **Risk-cache correction propagation.** The current historical cache retains
   existing same-version dates; its source hash records metadata/counts rather
   than all source values. Add source-value fingerprints and invalidation for
   corrected NAV, FX, benchmark, positions and risk-free observations; explicitly
   distinguish immutable published snapshots from recalculable analytics.
   Composite risk/crisis coverage must be validated before resuming Alpha World.
4. **Controlled release pipeline.** Wire the existing tests and new image smoke
   gate into the chosen private repository workflow when repository setup
   resumes. Add secret, OS/container and Node dependency scanning; SBOMs; template
   hashes; release manifests; isolated staging tests; production approval; and
   automatic registration of a verified rollback target. No Azure DevOps
   organization or pipeline was activated during this audit.
5. **Security hardening.** Finish the tested database-only authorization cutover
   to retire the explicitly documented owner fallback. Review every identity's
   role scope, add access-revocation integration tests, review Entra MFA/session
   policy, and assess private endpoints/firewalls for storage and Key Vault.
   Network hardening requires an egress/DNS design so it does not break jobs.
6. **Observability.** Add PostgreSQL/Key Vault diagnostic exports with redaction,
   slow-query thresholds, dead-letter age, freshness and missed-schedule alerts,
   and resource-tag/cost dashboards. Failure alerts alone cannot detect a job
   that never starts or one that succeeds without completing useful work.
7. **Maintainability and scale.** Split the large web module into bounded routes
   and service layers; centralize typed account contracts and report layouts;
   add realistic concurrent-user and many-account load tests; retain strict
   account-scoped report/download integrity checks. Retire obsolete one-off jobs
   only after their recovery purpose is documented.
8. **Financial and visual acceptance.** Independently reconcile representative
   month-end NAV/TWR/cash/FX/derived sleeves against IBKR and approved methodology.
   Add pixel and geometry regression tests for each approved template family,
   not only successful PDF/PPTX parsing. Full historical visual equivalence is
   not established by the 414 code tests. Keep Alpha World MS paused until its
   design-authority acceptance and composite analytics are signed off.

Bloomberg integration remains deferred as requested. No HA tier, VM, premium
registry or other substantial recurring-cost increase was introduced.

## Release verification

Local suite: 414 tests passed. Policy scan: 293 tracked files and 53 contiguous
migrations passed the existing repository/secret-pattern checks. Those checks
are limited pattern scans, not comprehensive security certification.

Repaired image: `ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:8f3a771c67ce4dc5f98aa425f5e6bf4f1b77981447538ed2d165df25b774aa64`.
ACR build `ck7m` succeeded. The deployed web revision is
`ibkrv2stg-web--0000110`. The same digest is pinned to historical-reports,
onboard-render, month-end-factsheet, analyst-assurance and burn-in-monitor.
Existing images were retained for recovery.

Live checks completed:

- Azure execution `ibkrv2stg-historical-reports-5tx2m2n` succeeded and logged:
  `PASS: report templates, PowerPoint runtime and native chart canvas`.
- `ibkrv2stg-month-end-factsheet-h2qu6hc` succeeded for all three active subjects.
  Each September package was already generated, so the idempotent workflow
  preserved it. This was not a new full historical rendering run.
- `ibkrv2stg-analyst-assurance-b8flxg5` succeeded: 11 passed checks, no warnings
  or failures for each of China & Hong Kong, U16088571 and U16088606. Its HKT
  assurance date is 2 October because execution crossed 16:00 UTC on 1 October.
- Authenticated owner-browser checks passed for Accounts, System health,
  Administration, monthly-report controls and the China & Hong Kong risk page.
  The latter displayed risk-free rate, Sharpe, tail attribution and crisis replay.
  Anonymous access to `/api/system/health` returned HTTP 401.
- System health now displays three tracked NAV subjects and three individual
  assurance cards. No approval or release checkbox was changed.
- The automatic rollback baseline was updated to revision 0000110 by successful
  one-off execution `ibkrv2stg-burn-in-monitor-fy1fhfu`; the Administration page
  confirmed it. The monthly authority deadline remains 15 October 2026,
  23:59:59 UTC. Approval remains an owner action.
- The original daily job remains a Schedule trigger at `0 8 * * *`, zero retries
  (16:00 HKT). The diagnostic job retained its original scheduled command,
  17:15 HKT schedule and zero retries after the one-off override.

The release guard required investigation for the web rollout. Comparing old
and new revision templates plus the Azure Resource Graph change at
2026-10-01T16:21:11.427Z established that only the image and Azure-managed
revision/traffic pointers changed. No environment, role, scale or schedule
change appeared in that record. The secret-free rollback record is
`.work/system-audit-20261001/release-20261001T161726Z.json`.

One baseline-registration test, `ibkrv2stg-burn-in-monitor-smbv9qc`, failed with
`KeyError: DATABASE_URL`: Azure's one-off environment override replaced rather
than merged the environment. It stopped before any database connection or
write. The corrected execution explicitly preserved the existing secret
reference and succeeded. A failure alert for that diagnostic execution may be
delivered; it is not a daily-ingestion failure. Never use a partial `--env-vars`
override when a one-off job needs its existing database secret reference.

Not performed: a PostgreSQL restore drill, destructive failover, deliberate
live-account access revocation, sign-in as each colleague, OS/Node/container
vulnerability certification, an independent recalculation of all historical
financial figures, or pixel-perfect validation of every historical report.
These remain explicit acceptance work, not implied successes.
