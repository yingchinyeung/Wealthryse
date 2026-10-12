"""One-time, additive login provisioning from existing Key Vault references.

No existing login/password/grant is replaced. Rollback on pre-commit errors;
post-commit verification failure leaves a restricted login, never broad grants.
"""
import json
import os
import sys
from urllib.parse import urlsplit, unquote
import psycopg
from psycopg import sql
from psycopg.rows import dict_row


def main():
    access = module("provision_access", SOURCE_ACCESS)
    target = urlsplit(os.environ["MONITOR_DATABASE_URL"])
    if target.username != "ibkr_metadata_monitor" or target.hostname != "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com" or target.path != "/ibkr_staging":
        raise RuntimeError("Unexpected monitor database target")
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=20,
                        options="-c statement_timeout=60000 -c lock_timeout=5000") as connection:
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname='ibkr_metadata_monitor'").fetchone():
            raise RuntimeError("Monitor login already exists; refusing alteration")
        connection.execute(sql.SQL("CREATE ROLE ibkr_metadata_monitor LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2 PASSWORD {}").format(sql.Literal(unquote(target.password))))
        connection.execute("ALTER ROLE ibkr_metadata_monitor SET default_transaction_read_only=on")
        connection.execute("ALTER ROLE ibkr_metadata_monitor SET statement_timeout='60s'")
        connection.execute("ALTER ROLE ibkr_metadata_monitor SET idle_in_transaction_session_timeout='60s'")
        connection.execute("GRANT CONNECT ON DATABASE ibkr_staging TO ibkr_metadata_monitor")
        for statement in access.grant_statements("ibkr_metadata_monitor"):
            connection.execute(statement)
    with psycopg.connect(os.environ["MONITOR_DATABASE_URL"], connect_timeout=20, row_factory=dict_row) as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id',%s,true)", (os.environ["PORTFOLIO_TENANT_ID"],))
        verify_access(connection, access)
        collector = module("provision_collector", SOURCE_COLLECTOR)
        result = collector.collect(connection)
    print('METADATA_PROVISION ' + json.dumps({"status":"pass", "role":"ibkr_metadata_monitor",
        "metadata_sections":len(result), "denied_columns":len(access.DENIED_COLUMNS), "application_writes":False}), flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print('METADATA_PROVISION ' + json.dumps({"status":"fail", "error_type":type(error).__name__}), flush=True)
        sys.exit(1)
