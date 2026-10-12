# Production Baseline Audit — 22 September 2026

## Scope

Read-only inspection of the existing Azure deployment and local release before development of the private portfolio website and AI-assisted reporting platform.

## Azure production baseline

- Tenant user observed: `anthony.ying@wealthryse.ai`
- Subscription: `3956e821-cee4-403f-8708-e84e2ed490eb`
- Resource group: `rg-ibkr-database-v11`
- Region: East Asia
- Scheduled job: `ibkr-v11-daily`
- Trigger: schedule
- Cron: `0 8 * * *` UTC, equivalent to 16:00 HKT
- Replica retry limit: 0
- Parallelism: 1
- Completion count: 1
- Replica timeout: 3,600 seconds

The resource group displayed 16 resources, including the daily, historical-backfill and bootstrap jobs; PostgreSQL, storage, Key Vault, managed identity, Container Apps environment, Log Analytics, and monitoring resources.

## Live execution evidence

The scheduled execution `ibkr-v11-daily-29834400`:

- started at 2026-09-22 08:00:00 UTC (16:00:00 HKT)
- completed at 2026-09-22 08:02:30 UTC
- status: Succeeded
- dashboard generation: PASS
- analyst SQLite and CSV exports: PASS
- exported snapshot: 26 tables and 247,410 rows
- final application status: COMPLETE

The twenty executions shown for 3–22 September 2026 all had Azure status `Succeeded`.

## Local source baseline

- Source copied from `release-hkc-cash-ledger-r14` into this isolated workspace.
- No raw Flex XML, SQLite snapshots, generated dashboards, logs, backups, or release archives were copied.
- Sensitive and generated artifacts are excluded by `.gitignore`.
- Test command: set `PYTHONPATH` to `src`, then run `python -m unittest discover -s tests -v`.
- Test result: 39 of 39 passed.

The passing suite covers ingestion idempotency, Flex retry/circuit-breaker safety, PostgreSQL adapter behavior, tenant isolation definitions, 16:00 HKT schedule declarations, immutable production-image controls, reference data, risk-free-rate publication guards, derived portfolios, dashboard generation, analyst exports, and cloud file content types.

## Findings

1. Production is currently healthy and should remain untouched while staging is built.
2. The live job configuration matches the intended 16:00 HKT schedule and lockout-safety controls.
3. `scripts/check_azure_status.ps1` contained stale user-facing text referring to 06:00 HKT and a suspended job. The v2 copy has been corrected to derive the displayed HKT time from the live cron response and to report the actual trigger type.
4. The source directory was not under Git. This v2 workspace is intended to become the controlled source baseline before any staging deployment.
5. `pytest` is not installed on this computer, but the project uses `unittest`-compatible tests and all 39 pass with the command documented in the root README.

## Production protection decision

No Azure resource, schedule, database, image, permission, secret, or production artifact was changed during this audit.

