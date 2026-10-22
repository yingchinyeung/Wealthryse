# Retirement candidates

Prepared 2026-10-12 for review. **Nothing here has been deleted or stopped.**
Each item needs the owner's go-ahead before anything is removed.

**Update, 21 Oct 2026:** v1.1 was deleted: its vault, storage, registry,
Postgres server, environment, jobs, identity, workspace, action group and
alerts, with its data archived into v2 storage first. Its network stays.
account-sync, which only imported v1.1's export, and institutional-shadow,
which compared v2's schedules with v1.1's daily job, were retired too: their
jobs and Bicep files are gone. institutional-shadow's script and its table
stay, because the website still shows the last reconciliation.

## How this list was made

A module counts as "in use" if the live platform can reach it. That means it
is imported, directly or through other modules, by one of these:

- the website (`ibkr_etl.web`), the credential broker or the CLI;
- one of the 25 scripts that the live Azure jobs run, including scripts they
  launch by path.

The job commands were read from Azure on 2026-10-12.

**Caveat:** seven jobs run inline Python programs that are stored in the job
definition, not in this repository. They are metadata-monitor, agent-audit,
authority, observe-reader, inbox-acceptance, inbox-acceptance-r2 and
secret-metadata. The specialist, metadata-monitor and inbox modules below may
be used by those programs, so check them before removing anything.

## Code: 90 of 188 `ibkr_etl` modules (13,832 lines) are not reachable

### Owner Codex / Codex runtime trials (16 modules, 5,303 lines)

`owner_codex_worker`, `owner_codex_tasks`, `owner_codex_trial_lifecycle`, `owner_codex_trial_journal`, `codex_runtime_probe`, `codex_exec_probe`, `owner_codex_catalog_transport`, `owner_codex_permissions`, `owner_codex_worker_audit`, `owner_codex_execution_fence`, `owner_codex_trial_journal_local`, `owner_codex_isolated_target`, `codex_workspace`, `codex_compatibility`, `owner_codex_trial_journal_azure`, `owner_codex_catalog_snapshot`

### Specialist agents (audit, authority, observation, gateway) (22 modules, 2,225 lines)

`agent_observation`, `agent_governance`, `agent_gateway_auth`, `specialist_autonomy`, `specialist_evidence`, `agent_gateway`, `agent_observation_audit`, `specialist_capture`, `specialist_authority_runtime`, `specialist_audit_runtime`, `specialist_observation_access`, `specialist_archive_reader`, `specialist_audit_service`, `specialist_observation_runtime`, `specialist_report_runtime`, `agent_authority`, `specialist_capture_verifier`, `specialist_gateway_runtime`, `agent_observation_evidence`, `agent_gateway_limits`, `specialist_audit`, `specialist_service_access`

### Agent daily report / inbox / mail (11 modules, 886 lines)

`agent_daily_report`, `agent_daily_worker`, `agent_report_notifications`, `agent_daily_graph_mail`, `agent_daily_outbox`, `agent_daily_inbox_ui`, `agent_daily_inbox`, `agent_daily_delivery`, `agent_report_watchdog`, `agent_daily_readiness`, `agent_daily_schedule`

### Evidence and workflow bookkeeping (28 modules, 3,787 lines)

`fx_evidence_import`, `workflow_lineage`, `cashflow_source_evidence`, `workflow_ledger`, `comparison_source_evidence`, `workflow_financial_inputs`, `snapshot_atomic_import`, `workflow_leases`, `snapshot_evidence_import`, `snapshot_preflight`, `comparison_cashflow_evidence`, `release_package`, `source_archive`, `archive_evidence_rehearsal`, `workflow_risk_evidence`, `workflow_fx_dependencies`, `fx_import_lifecycle`, `workflow_dates`, `build_inputs`, `workflow_readiness`, `position_recovery`, `protected_release_reader`, `release_evidence`, `fx_evidence_bundle`, `risk_execution_shape`, `fx_source_evidence`, `fx_evidence_contract`, `risk_base_recipe`

### Monitoring and operations (13 modules, 1,631 lines)

`identity_registry`, `monitor_release`, `operational_monitor`, `report_design_worker`, `container_scan`, `resource_cost_workbook`, `schedule_monitor`, `azure_inventory`, `operational_alerts`, `minimal_runtime`, `model_cash_display`, `monthly_schedule_monitor`, `metadata_monitor_access`

The tests for these modules would go with them.

Separately, `scripts/` holds 1,338 files. Only 39 are reached by a live job.
The other 1,208 non-test scripts (106,297 lines) are dated one-off probes,
controls, upgrades and qualifications, such as `probe_upgrade_*_20261009.py`.
They could move to an archive branch.

## Infrastructure files with no live resource

Six `infra/` files describe one-off upgrade jobs that no longer exist in Azure:

- `staging-factsheet-v6-upgrade-job.bicep`
- `staging-historical-factsheet-v6-job.bicep`
- `staging-historical-presentation-v24-job.bicep`
- `staging-historical-report-job.bicep`
- `staging-nav-axis-upgrade-job.bicep`
- `staging-presentation-v24-upgrade-job.bicep`

## Azure resources to review

These servers are in rg-ibkr-database-v11. All 7 PostgreSQL servers have
public access disabled.

| Resource | What it appears to be | Suggested action |
|---|---|---|
| ibkrv2stg-pg-auhecwc4itaoo | Main production database | Keep |
| ibkrv11-pg-5rcc62xxqblay | v1.1 database used by the 3 legacy jobs | Deleted with v1.1 on 21 Oct 2026 |
| ibkrv2stg-upgrade-recovery-1009 | Private recovery host holding the QA databases `upgrade_g04_20261010` and `upgrade_g04_migration_20261011` (per the 2026-10-11 handover) | Keep until the stage 44 QA work is closed, then stop or delete |
| ibkrv2stg-ha-recovery-20261006 | Restore taken 6 Oct | Confirm unused, then stop or delete |
| ibkrv2stg-app-recovery-20261008 | Restore taken 8 Oct | Confirm unused, then stop or delete |
| ibkrv11-upgrade-recovery-1009 | v1.1 restore taken 9 Oct | Confirm unused, then stop or delete |
| ibkrv2stg-ownertrial-20261004 | Owner Codex trial database | Retire together with the Owner Codex modules |
| ibkrv2stg-private-admin (VM) | Private admin host | Keep if used for private database access |

Stopping a Flexible Server keeps its data and stops compute charges, but
Azure restarts it automatically after 7 days. Deleting it is permanent unless
a backup is retained.
