"""Read-only recovery evidence. No credentials or financial values are emitted.

Run on the existing private network with DATABASE_URL supplied by a secret ref.
RECOVERY_HOST may only select the dedicated drill server, never an arbitrary host.
This does not run migrations, change report approvals or connect any producers.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

import psycopg
from psycopg import sql


RESTORE_HOST = "ibkrv2stg-recovery-20261001.postgres.database.azure.com"
TABLES = (
    "schema_migrations", "nav_snapshots", "position_snapshots", "cash_transactions",
    "derived_portfolio_nav", "derived_portfolio_cash_ledger",
    "reporting_snapshots", "report_generation_runs", "report_artifact_releases",
    "subject_report_template_assets", "platform_subjects",
)


def main() -> None:
    override = os.environ.get("RECOVERY_HOST", "")
    if override and override != RESTORE_HOST:
        raise RuntimeError("Recovery host is not the explicitly isolated drill server")
    kwargs = {"host": override} if override else {}
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30,
                         options="-c default_transaction_read_only=on -c statement_timeout=120000",
                         **kwargs) as connection:
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id', %s, true)",
                           (os.environ["PORTFOLIO_TENANT_ID"],))
        metadata = connection.execute(
            "SELECT current_database(), current_setting('transaction_read_only'), "
            "current_setting('server_version'), transaction_timestamp()::text"
        ).fetchone()
        columns = connection.execute(
            "SELECT table_name,column_name,data_type,is_nullable,column_default "
            "FROM information_schema.columns WHERE table_schema='public' "
            "ORDER BY table_name,ordinal_position"
        ).fetchall()
        digest = hashlib.sha256(json.dumps(columns, default=str).encode()).hexdigest()
        evidence = {"mode": "restored" if override else "source", "database": metadata[0],
                    "read_only": metadata[1], "version": metadata[2],
                    "captured_at": metadata[3], "schema_sha256": digest, "tables": {}}
        for table in TABLES:
            # Order row digests, not raw personal/account data. Duplicates remain counted.
            query = sql.SQL("SELECT md5(row_to_json(t)::text) FROM {} t ORDER BY 1").format(
                sql.Identifier("public", table))
            row_hash = hashlib.sha256()
            count = 0
            with connection.cursor(name="recovery_rows") as cursor:
                cursor.execute(query)
                for row in cursor:
                    row_hash.update((row[0] + "\n").encode())
                    count += 1
            evidence["tables"][table] = {"rows": count, "sha256": row_hash.hexdigest()}
        evidence["latest_nav_date"] = connection.execute(
            "SELECT MAX(snapshot_date) FROM nav_snapshots").fetchone()[0]
        evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
        print("RECOVERY_EVIDENCE " + json.dumps(evidence, sort_keys=True, default=str), flush=True)


if __name__ == "__main__":
    main()
