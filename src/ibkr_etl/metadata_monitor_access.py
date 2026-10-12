"""Explicit metadata column grants for the proposed dedicated monitor role.

No table-wide/default grants, passwords, financial values or workflow payloads.
Deployment must independently establish identity/tenant isolation and audit
PUBLIC privileges before a login is attached to this role.
"""
from __future__ import annotations


COLUMNS = {
    "platform_subjects": "tenant_id subject_id subject_type lifecycle_status reporting_currency",
    # Avoid the security-invoker financial view: its definition also references
    # balances. Read only the same dated rows, never grant those balance columns.
    "accounts": "tenant_id account_id",
    "nav_snapshots": "account_id snapshot_date asset_class",
    "derived_portfolio_nav": "tenant_id portfolio_id snapshot_date",
    "risk_analytics_snapshots": "tenant_id subject_id as_of_date analytics_version comparison_version quality_status",
    "subject_operating_registrations": "tenant_id subject_id capability operating_state effective_from effective_to",
    "portfolio_benchmark_mapping": "tenant_id subject_id benchmark_id mapping_type reporting_currency effective_from effective_to",
    "benchmark_master": "tenant_id benchmark_id currency",
    "benchmark_prices": "tenant_id benchmark_id observation_date observation_status quality_status",
    "performance_comparison_daily": "tenant_id subject_id benchmark_id reporting_currency calculation_version observation_date quality_status",
    "reference_fx_rates": "tenant_id from_currency to_currency provider rate_date quality_status",
    "risk_free_rates": "tenant_id currency series_id provider rate_date quality_status",
    "institutional_work_items": "tenant_id work_item_id subject_id work_kind status available_at created_at updated_at started_at completed_at lease_expires_at attempt_count maximum_attempts depends_on_work_item_id",
    "workflow_outbox": "tenant_id event_id aggregate_type status available_at created_at lease_expires_at attempt_count maximum_attempts",
}

DENIED_COLUMNS = (
    ("nav_snapshots", "total_text"), ("benchmark_prices", "level"),
    ("risk_free_rates", "annual_rate"), ("risk_analytics_snapshots", "payload_json"),
    ("institutional_work_items", "payload_json"), ("workflow_outbox", "payload_json"),
    ("ibkr_flex_credentials", "key_vault_secret_name"),
)


def grant_statements(role: str) -> list[str]:
    # Deliberately not a general SQL identifier escaper or arbitrary grant tool.
    if role not in {"ibkr_metadata_monitor", "ibkr_metadata_monitor_trial"}:
        raise ValueError("Unapproved monitor role")
    return ["GRANT USAGE ON SCHEMA public TO " + role] + [
        f"GRANT SELECT ({', '.join(columns.split())}) ON public.{table} TO {role}"
        for table, columns in sorted(COLUMNS.items())
    ]
