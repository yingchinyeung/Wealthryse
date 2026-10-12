"""Account-scoped READ ONLY conservative FX dependency observation."""
import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.workflow_financial_inputs import load_bundle
from ibkr_etl.workflow_fx_dependencies import trace_fx_dependencies


def main(argv=None):
    parser = argparse.ArgumentParser();parser.add_argument("--as-of", required=True)
    args = parser.parse_args(argv)
    cutoff = date.fromisoformat(args.as_of)
    if cutoff > datetime.now(timezone.utc).date(): parser.error("Future cutoff prohibited")
    import psycopg
    tenant = os.environ["PORTFOLIO_TENANT_ID"]
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20,
            options="-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000",
            row_factory=_hybrid_row_factory) as raw:
        raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        raw.execute("SELECT set_config('app.tenant_id',%s,true)", (tenant,))
        connection = PostgresConnection(raw)
        accounts = connection.execute("""SELECT a.account_id,a.base_currency,s.history_start_date
            FROM accounts a JOIN platform_subjects s ON s.tenant_id=a.tenant_id AND s.subject_id=a.account_id
            WHERE a.tenant_id=? AND s.subject_type='physical_account' AND s.lifecycle_status='active'
            AND s.history_start_date<=? ORDER BY a.account_id""", (tenant,args.as_of)).fetchall()
        results = [trace_fx_dependencies(load_bundle(connection, tenant_id=tenant,
            account_id=row["account_id"], start=str(row["history_start_date"]), as_of=args.as_of),
            base_currency=row["base_currency"]) for row in accounts]
        raw.rollback()
    print("WORKFLOW_FX_DEPENDENCY_OBSERVATION " + json.dumps({"probe_complete": True,
        "as_of": args.as_of, "database_read_only": True, "accounts": results,
        "source_changes": False, "execution_authorized": False}, sort_keys=True, allow_nan=False), flush=True)
    return 0 if results else 1


if __name__ == "__main__": raise SystemExit(main())
