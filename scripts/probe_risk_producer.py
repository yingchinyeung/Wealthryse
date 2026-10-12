"""Fixed-date read-only producer rehearsal for ACTIVE accounts; never writes.

The 2026-09-30 date is this reviewed diagnostic's scope, not a general scheduler
calendar. Emits fingerprints/status only, not financial values or credentials.
"""
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))
import psycopg
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.workflow_risk_evidence import inspect_risk_output


def main():
    tenant = os.environ["PORTFOLIO_TENANT_ID"]
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20,
            options="-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000",
            row_factory=_hybrid_row_factory) as raw:
        raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        raw.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        connection = PostgresConnection(raw)
        subjects = raw.execute("""SELECT subject_id FROM platform_subjects
            WHERE tenant_id=%s AND lifecycle_status='active' ORDER BY subject_id""", (tenant,)).fetchall()
        results = []
        for row in subjects:
            result = inspect_risk_output(connection, tenant_id=tenant, subject_id=row["subject_id"],
                as_of="2026-09-30", expected_comparison_date=date(2026, 9, 30))
            results.append(result)
            print("RISK_PRODUCER " + json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
        print("RISK_PRODUCER " + json.dumps({"probe_complete": True, "subjects": len(results),
            "outputs_verified": sum(r["output_verified"] for r in results),
            "database_read_only": raw.execute("SHOW transaction_read_only").fetchone()[0] == "on",
            "execution_authorized": False, "prerequisites_verified": False}), flush=True)
        raw.rollback()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Exception text may contain SQL values/connection details. Never log it.
        print("RISK_PRODUCER " + json.dumps({"probe_complete": False,
            "error_type": type(error).__name__, "execution_authorized": False}), flush=True)
        raise SystemExit(1)
