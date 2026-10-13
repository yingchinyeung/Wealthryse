"""Explicit grants for the live collector's database role.

The collector on the gateway VM may write the seven live_ tables from
migration 063 and nothing else: no other table, no view, no function beyond the
tenant setting, and no default privileges. The website reads the same tables
through its existing read-only role.
"""
from __future__ import annotations


ROLE = "ibkr_live_collector"

LIVE_TABLES = (
    "live_gateway_status",
    "live_account_values",
    "live_positions",
    "live_quotes",
    "live_executions",
    "live_daily_bars",
    "live_contracts",
)

# Upserts need SELECT and UPDATE; DELETE is for the 30-day and 1-year
# retention purges, and live_positions rows go with their account snapshot.
TABLE_PRIVILEGES = "SELECT, INSERT, UPDATE, DELETE"

ROLE_SETTINGS = (
    "ALTER ROLE ibkr_live_collector SET statement_timeout = '60s'",
    "ALTER ROLE ibkr_live_collector SET idle_in_transaction_session_timeout = '60s'",
    "ALTER ROLE ibkr_live_collector SET default_transaction_read_only = off",
)

ROLE_ATTRIBUTES = (
    "LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
    "NOBYPASSRLS CONNECTION LIMIT 3"
)


def grant_statements(role: str = ROLE) -> list[str]:
    # Deliberately not a general SQL identifier escaper or arbitrary grant tool.
    if role not in {ROLE, "ibkr_live_collector_trial"}:
        raise ValueError("Unapproved live collector role")
    return [
        f"GRANT USAGE ON SCHEMA public TO {role}",
        f"GRANT EXECUTE ON FUNCTION public.app_tenant_id() TO {role}",
    ] + [
        f"GRANT {TABLE_PRIVILEGES} ON TABLE public.{table} TO {role}"
        for table in LIVE_TABLES
    ]


def provision(connection, *, password: str, database: str, role: str = ROLE) -> None:
    """Create the login once and grant it the live tables. Never alters an existing role."""
    from psycopg import sql

    grants = grant_statements(role)
    if len(password) < 24:
        raise ValueError("The live collector password must have at least 24 characters")
    if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
        raise RuntimeError("The live collector login already exists; refusing alteration")
    connection.execute(
        sql.SQL("CREATE ROLE {} " + ROLE_ATTRIBUTES + " PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(password)
        )
    )
    for statement in ROLE_SETTINGS:
        connection.execute(statement.replace(ROLE, role, 1))
    connection.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database), sql.Identifier(role)
        )
    )
    for statement in grants:
        connection.execute(statement)


def privilege_report(connection, role: str = ROLE) -> dict[str, object]:
    """What the role can do, for the post-provisioning check."""
    grant_statements(role)
    writable = connection.execute(
        """
        SELECT c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'm', 'p')
          AND (has_table_privilege(%s, c.oid, 'INSERT')
               OR has_table_privilege(%s, c.oid, 'UPDATE')
               OR has_table_privilege(%s, c.oid, 'DELETE'))
        ORDER BY c.relname
        """,
        (role, role, role),
    ).fetchall()
    readable = connection.execute(
        """
        SELECT c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v', 'm', 'p')
          AND has_table_privilege(%s, c.oid, 'SELECT')
        ORDER BY c.relname
        """,
        (role,),
    ).fetchall()
    attributes = connection.execute(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = %s",
        (role,),
    ).fetchone()
    return {
        "writable": [row[0] for row in writable],
        "readable": [row[0] for row in readable],
        "privileged_attributes": bool(attributes and any(attributes)),
    }


def least_privilege_problems(report: dict[str, object]) -> list[str]:
    problems = []
    if report["privileged_attributes"]:
        problems.append("role has superuser, bypass-RLS or create attributes")
    if sorted(report["writable"]) != sorted(LIVE_TABLES):
        problems.append("writable tables differ from the seven live tables")
    if sorted(report["readable"]) != sorted(LIVE_TABLES):
        problems.append("readable tables differ from the seven live tables")
    return problems
