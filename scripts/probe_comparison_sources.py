"""Read-only, fixed 2026-09-30 source projection check. No provider calls."""
import json
import os
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "src"))
import psycopg
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.comparison_source_evidence import load_source_projection, verify_sources


def main():
    tenant = os.environ["PORTFOLIO_TENANT_ID"]
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20,
            options="-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000",
            row_factory=_hybrid_row_factory) as raw:
        raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        raw.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        connection = PostgresConnection(raw)
        targets = raw.execute("""SELECT m.subject_id, m.benchmark_id FROM platform_subjects s
            JOIN portfolio_benchmark_mapping m ON m.tenant_id=s.tenant_id AND m.subject_id=s.subject_id
            WHERE s.tenant_id=%s AND s.lifecycle_status='active' AND m.effective_from<='2026-09-30'
            AND (m.effective_to IS NULL OR m.effective_to>='2026-09-30') ORDER BY m.subject_id, m.benchmark_id""", (tenant,)).fetchall()
        results = []
        for target in targets:
            data = load_source_projection(connection, tenant_id=tenant, subject_id=target["subject_id"],
                benchmark_id=target["benchmark_id"], as_of="2026-09-30")
            result = verify_sources(data, expected_date=date(2026, 9, 30))
            results.append(result)
            print("COMPARISON_SOURCE " + json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
        print("COMPARISON_SOURCE " + json.dumps({"probe_complete": True, "mappings": len(results),
            "projections_verified": sum(r["source_projection_verified"] for r in results),
            "database_read_only": raw.execute("SHOW transaction_read_only").fetchone()[0] == "on",
            "execution_authorized": False}), flush=True)
        raw.rollback()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("COMPARISON_SOURCE " + json.dumps({"probe_complete": False,
            "error_type": type(error).__name__, "execution_authorized": False}), flush=True)
        raise SystemExit(1)
