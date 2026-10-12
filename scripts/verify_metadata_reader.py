"""Transactionally rehearse restricted metadata grants; always roll back.

Launcher supplies SOURCE_COLLECTOR and SOURCE_ACCESS source strings in memory.
No login/password/secret or permanent permission change is created by this test.
"""
import json
import os
import types
import psycopg
from psycopg import sql
from psycopg.rows import dict_row


def main():
    collector = types.ModuleType("metadata_collector")
    access = types.ModuleType("metadata_access")
    exec(compile(SOURCE_COLLECTOR, "metadata-collector", "exec"), collector.__dict__)
    exec(compile(SOURCE_ACCESS, "metadata-access", "exec"), access.__dict__)
    role = "ibkr_metadata_monitor_trial"
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30,
                         options="-c statement_timeout=60000 -c lock_timeout=5000",
                         autocommit=True, row_factory=dict_row) as connection:
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            raise RuntimeError("Trial role already exists; no permission changes attempted")
        with connection.transaction(force_rollback=True):
            connection.execute("SELECT set_config('app.tenant_id', %s, true)",
                               (os.environ["PORTFOLIO_TENANT_ID"],))
            connection.execute("CREATE ROLE ibkr_metadata_monitor_trial NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
            for statement in access.grant_statements(role):
                connection.execute(statement)
            connection.execute("GRANT ibkr_metadata_monitor_trial TO CURRENT_USER")
            connection.execute("SET LOCAL ROLE ibkr_metadata_monitor_trial")
            if connection.execute("SELECT current_user AS role").fetchone()["role"] != role:
                raise RuntimeError("Restricted role not active")
            result = collector.collect(connection)
            denied_checks = 0
            for table, column in access.DENIED_COLUMNS:
                try:
                    with connection.transaction():
                        # LIMIT 0 verifies authorization without retrieving a value.
                        connection.execute(sql.SQL("SELECT {} FROM public.{} LIMIT 0").format(
                            sql.Identifier(column), sql.Identifier(table)))
                except psycopg.errors.InsufficientPrivilege:
                    denied_checks += 1
                else:
                    raise RuntimeError("Forbidden column was readable: " + table + "." + column)
            writes = connection.execute("""
                SELECT COUNT(*) AS count FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m')
                  AND (has_table_privilege(current_user,c.oid,'INSERT')
                    OR has_table_privilege(current_user,c.oid,'UPDATE')
                    OR has_table_privilege(current_user,c.oid,'DELETE')
                    OR has_table_privilege(current_user,c.oid,'TRUNCATE'))
            """).fetchone()["count"]
            if writes:
                raise RuntimeError("Restricted role inherited public-schema writes")
            definers = connection.execute("""
                SELECT COUNT(*) AS count FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE n.nspname='public' AND p.prosecdef
                  AND has_function_privilege(current_user,p.oid,'EXECUTE')
            """).fetchone()["count"]
            if definers:
                raise RuntimeError("Unreviewed callable SECURITY DEFINER functions")
            connection.execute("RESET ROLE")
        if connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            raise RuntimeError("Trial rollback was not confirmed")
    print("METADATA_READER_EVIDENCE " + json.dumps({
        "status":"pass", "metadata_sections":len(result), "subject_count":len(result["subjects"]),
        "denied_column_checks":denied_checks, "write_privileges":writes,
        "callable_public_definers":definers, "trial_role":"rolled_back",
        "limits":["No login or scheduled monitor deployed", "Tenant authentication binding remains to be implemented"]}), flush=True)


if __name__ == "__main__":
    main()
