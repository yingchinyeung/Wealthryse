"""Create the read-only database login for the rebalancing website.

Runs once as a Container Apps job inside ibkrv2stg-env (the database is only
reachable from there). Needs DATABASE_ADMIN_URL, READER_PASSWORD and
EXPECTED_DATABASE. Prints one JSON line and exits non-zero if the login could
write anything.
"""

import json
import os
import sys

import psycopg
from psycopg import conninfo, sql

ROLE = "ibkr_rebalance_reader"
# Sign-in, user and broker-credential metadata stay hidden from the site.
EXCLUDED = (
    "app_users",
    "app_user_roles",
    "user_account_access",
    "broker_connections",
    "broker_connection_accounts",
    "ibkr_flex_credentials",
    "ibkr_credential_circuit_state",
    "owner_codex_sessions",
)


def main() -> int:
    admin_url = os.environ["DATABASE_ADMIN_URL"]
    password = os.environ["READER_PASSWORD"]
    expected_db = os.environ["EXPECTED_DATABASE"]
    if len(password) < 24:
        raise RuntimeError("READER_PASSWORD must contain at least 24 characters")

    role = sql.Identifier(ROLE)
    with psycopg.connect(admin_url) as conn:
        dbname = conn.info.dbname
        if dbname != expected_db:
            raise RuntimeError(f"admin URL points at {dbname}, expected {expected_db}")
        if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,)).fetchone() is None:
            conn.execute(sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOREPLICATION NOBYPASSRLS NOINHERIT CONNECTION LIMIT 10"
            ).format(role))
        conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(role, sql.Literal(password)))
        conn.execute(sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role))
        conn.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '60s'").format(role))
        conn.execute(sql.SQL("ALTER ROLE {} SET idle_in_transaction_session_timeout = '60s'").format(role))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), role))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(role))
        conn.execute(sql.SQL(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}"
        ).format(role))
        for table in EXCLUDED:
            if conn.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()[0]:
                conn.execute(sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                    sql.Identifier("public", table), role))

        writable = [r[0] for r in conn.execute(
            "SELECT n.nspname || '.' || c.relname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r','p','v','m','f') "
            "AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_%%' "
            "AND has_table_privilege(%s, c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')", (ROLE,))]
        creatable = [r[0] for r in conn.execute(
            "SELECT nspname FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema') "
            "AND nspname NOT LIKE 'pg_%%' AND has_schema_privilege(%s, nspname, 'CREATE')", (ROLE,))]
        definer_functions = [r[0] for r in conn.execute(
            "SELECT n.nspname || '.' || p.proname FROM pg_proc p "
            "JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema') "
            "AND has_function_privilege(%s, p.oid, 'EXECUTE')", (ROLE,))]
        readable = conn.execute(
            "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
            "AND has_table_privilege(%s, schemaname || '.' || tablename, 'SELECT')", (ROLE,)).fetchone()[0]

    # Sign in as the new login: reading works, writing is refused.
    params = conninfo.conninfo_to_dict(admin_url)
    params.update(user=ROLE, password=password)
    with psycopg.connect(conninfo.make_conninfo(**params)) as reader:
        tenants = reader.execute("SELECT count(*) FROM tenants").fetchone()[0]
        try:
            reader.execute("SET TRANSACTION READ WRITE")
            reader.execute("UPDATE tenants SET tenant_id = tenant_id WHERE false")
            write_refused = False
        except psycopg.errors.InsufficientPrivilege:
            write_refused = True
        reader.rollback()

    ok = not writable and not creatable and write_refused
    print(json.dumps({
        "rebalance_reader": "ok" if ok else "FAILED",
        "database": dbname,
        "readable_public_tables": readable,
        "hidden_tables": list(EXCLUDED),
        "tenants_visible": tenants,
        "write_test_refused": write_refused,
        "writable_tables": writable,
        "schemas_with_create": creatable,
        "security_definer_functions_executable": definer_functions,
    }))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
