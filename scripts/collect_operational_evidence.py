"""Private-network, read-only operational evidence; no balances or payloads.

This is a diagnostic probe, not a scheduler or remediation worker. Its output
is allow-listed metadata. Absolute market freshness needs an approved calendar;
matching dates alone are not proof that calculations or source values are right.
"""
from __future__ import annotations

import json
import os

import psycopg
from psycopg.rows import dict_row


def collect(connection):
    def rows(query):
        return connection.execute(query).fetchall()

    result = {"schema": "operational-evidence-v1"}
    result["capture"] = rows("""
        SELECT transaction_timestamp() AS captured_at,
               current_setting('transaction_read_only') AS read_only,
               (transaction_timestamp() AT TIME ZONE 'Asia/Hong_Kong')::date AS hkt_date
    """)[0]
    result["subjects"] = rows("""
        SELECT s.subject_id, s.subject_type, s.lifecycle_status,
               s.reporting_currency,
               (SELECT MAX(n.snapshot_date) FROM (
                    SELECT v.snapshot_date FROM nav_snapshots v
                    JOIN accounts a ON a.account_id=v.account_id
                    WHERE a.tenant_id=s.tenant_id AND v.account_id=s.subject_id AND v.asset_class='TOTAL'
                    UNION ALL
                    SELECT d.snapshot_date FROM derived_portfolio_nav d
                    WHERE d.tenant_id=s.tenant_id AND d.portfolio_id=s.subject_id
                ) n) AS nav_date,
               (SELECT MAX(r.as_of_date) FROM risk_analytics_snapshots r
                WHERE r.tenant_id=s.tenant_id AND r.subject_id=s.subject_id
                  AND r.analytics_version='risk-analytics-v2'
                  AND r.comparison_version='benchmark-comparison-v1'
                  AND r.quality_status='pass') AS risk_pass_date,
               (SELECT MAX(r.as_of_date) FROM risk_analytics_snapshots r
                WHERE r.tenant_id=s.tenant_id AND r.subject_id=s.subject_id
                  AND r.analytics_version='risk-analytics-v2'
                  AND r.comparison_version='benchmark-comparison-v1'
                  AND r.quality_status IN ('pass','warn')) AS risk_usable_date,
               (SELECT MAX(r.as_of_date) FROM risk_analytics_snapshots r
                WHERE r.tenant_id=s.tenant_id AND r.subject_id=s.subject_id
                  AND r.analytics_version='risk-analytics-v2') AS risk_any_date
        FROM platform_subjects s WHERE s.tenant_id=app_tenant_id()
        ORDER BY s.subject_id
    """)
    result["registrations"] = rows("""
        SELECT subject_id, capability, operating_state, effective_from, effective_to
        FROM subject_operating_registrations WHERE tenant_id=app_tenant_id()
          AND effective_from <= (now() AT TIME ZONE 'Asia/Hong_Kong')::date
          AND (effective_to IS NULL OR effective_to >= (now() AT TIME ZONE 'Asia/Hong_Kong')::date)
        ORDER BY subject_id, capability
    """)
    result["benchmarks"] = rows("""
        SELECT m.subject_id, m.benchmark_id, m.mapping_type, m.reporting_currency,
               b.currency AS benchmark_currency,
               (SELECT MAX(p.observation_date) FROM benchmark_prices p
                WHERE p.tenant_id=m.tenant_id AND p.benchmark_id=m.benchmark_id
                  AND p.observation_status IN ('official_close','corrected')
                  AND p.quality_status='pass') AS benchmark_pass_date,
               (SELECT MAX(p.observation_date) FROM benchmark_prices p
                WHERE p.tenant_id=m.tenant_id AND p.benchmark_id=m.benchmark_id
                  AND p.observation_status IN ('official_close','corrected','provisional_close')
                  AND p.quality_status IN ('pass','warn')) AS benchmark_usable_date,
               (SELECT MAX(c.observation_date) FROM performance_comparison_daily c
                WHERE c.tenant_id=m.tenant_id AND c.subject_id=m.subject_id
                  AND c.benchmark_id=m.benchmark_id
                  AND c.reporting_currency=m.reporting_currency
                  AND c.calculation_version='benchmark-comparison-v1'
                  AND c.quality_status='pass') AS comparison_pass_date,
               (SELECT MAX(c.observation_date) FROM performance_comparison_daily c
                WHERE c.tenant_id=m.tenant_id AND c.subject_id=m.subject_id
                  AND c.benchmark_id=m.benchmark_id
                  AND c.reporting_currency=m.reporting_currency
                  AND c.calculation_version='benchmark-comparison-v1'
                  AND c.quality_status IN ('pass','warn')) AS comparison_usable_date
        FROM portfolio_benchmark_mapping m
        JOIN benchmark_master b ON b.tenant_id=m.tenant_id AND b.benchmark_id=m.benchmark_id
        WHERE m.tenant_id=app_tenant_id()
          AND m.effective_from <= to_char(now() AT TIME ZONE 'Asia/Hong_Kong','YYYY-MM-DD')
          AND (m.effective_to IS NULL OR m.effective_to >= to_char(now() AT TIME ZONE 'Asia/Hong_Kong','YYYY-MM-DD'))
        ORDER BY m.subject_id, m.mapping_type, m.benchmark_id
    """)
    result["fx"] = rows("""
        SELECT from_currency,to_currency,provider,quality_status,MAX(rate_date) AS latest_date
        FROM reference_fx_rates WHERE tenant_id=app_tenant_id()
        GROUP BY from_currency,to_currency,provider,quality_status ORDER BY 1,2,3,4
    """)
    result["risk_free"] = rows("""
        SELECT currency,series_id,provider,quality_status,MAX(rate_date) AS latest_date
        FROM risk_free_rates WHERE tenant_id=app_tenant_id()
        GROUP BY currency,series_id,provider,quality_status ORDER BY 1,2,3,4
    """)
    result["work_counts"] = rows("""
        SELECT status,COUNT(*) AS count FROM institutional_work_items
        WHERE tenant_id=app_tenant_id() GROUP BY status ORDER BY status
    """)
    result["work"] = rows("""
        SELECT w.work_item_id,w.subject_id,w.work_kind,w.status,w.available_at,
               w.created_at,w.updated_at,w.started_at,w.completed_at,w.lease_expires_at,
               w.attempt_count,w.maximum_attempts,
               w.depends_on_work_item_id IS NOT NULL AS has_dependency,
               d.status AS dependency_status,d.completed_at AS dependency_completed_at,
               s.lifecycle_status AS subject_state
        FROM institutional_work_items w
        LEFT JOIN institutional_work_items d
          ON d.tenant_id=w.tenant_id AND d.work_item_id=w.depends_on_work_item_id
        LEFT JOIN platform_subjects s ON s.tenant_id=w.tenant_id AND s.subject_id=w.subject_id
        WHERE w.tenant_id=app_tenant_id()
          AND w.status NOT IN ('succeeded','cancelled')
        ORDER BY w.created_at,w.work_item_id LIMIT 501
    """)
    result["outbox_counts"] = rows("""
        SELECT status,COUNT(*) AS count FROM workflow_outbox
        WHERE tenant_id=app_tenant_id() GROUP BY status ORDER BY status
    """)
    result["outbox"] = rows("""
        SELECT event_id,aggregate_type,status,available_at,created_at,lease_expires_at,
               attempt_count,maximum_attempts
        FROM workflow_outbox WHERE tenant_id=app_tenant_id() AND status <> 'published'
        ORDER BY created_at,event_id LIMIT 501
    """)
    # Never turn a truncated inventory into a claim that the queue is healthy.
    result["inventory_complete"] = len(result["work"]) <= 500 and len(result["outbox"]) <= 500
    return result


def main():
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30,
                         options="-c default_transaction_read_only=on -c statement_timeout=60000",
                         row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id', %s, true)",
                           (os.environ["PORTFOLIO_TENANT_ID"],))
        result = collect(connection)
        assert result["capture"]["read_only"] == "on"
        # Each row gets its own bounded line. Length and final markers prevent
        # missing/delayed/truncated log lines from implying an empty healthy queue.
        for section, value in result.items():
            if isinstance(value, list):
                for index, row in enumerate(value):
                    print("OPERATIONAL_EVIDENCE " + json.dumps(
                        {"section":section, "index":index, "row":row}, default=str, sort_keys=True), flush=True)
                record = {"section":section, "length":len(value)}
            else:
                record = {"section":section, "value":value}
            print("OPERATIONAL_EVIDENCE " + json.dumps(record, default=str, sort_keys=True), flush=True)
        print('OPERATIONAL_EVIDENCE {"section":"complete","value":true}', flush=True)


if __name__ == "__main__":
    main()
