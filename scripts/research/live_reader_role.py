"""The research_reader login on the live server: read-only, silver only, tenant row security.

The research job reads the live server through this login and nothing else (condition 4 of
the research zone, docs/RESEARCH_ZONE.md). It may read the silver tables and views and run
the U8 "as known on" functions. It cannot write anything, and it cannot read ops, credential,
gold or bronze tables. It never bypasses row security, so it sees one tenant's rows, chosen
by app.tenant_id as for the other logins. Research across tenants would need an owner policy
first.

  python scripts/research/live_reader_role.py --dry-run   prints the SQL; connects to nothing
  python scripts/research/live_reader_role.py --apply     creates the login and its grants
  python scripts/research/live_reader_role.py --check     prints every privilege the login has

Run --apply and --check from the private admin VM, like the other role scripts, with
DATABASE_ADMIN_URL (the live server's admin URL) and RESEARCH_LIVE_READER_URL (Key Vault
research-live-reader-url, which supplies the password, so the password is never typed or
printed). --apply runs in one transaction: it creates the login only if it does not exist
(an existing login keeps its password and settings), grants the list below, then runs the
privilege check and rolls everything back if the login can do more than it should.

The U8 history tables and functions arrive with their own migration. Grants on them are
given when they exist; when the migration comes later it copies SELECT on each table to its
history, and EXECUTE on functions is open to every login, so the reader needs no second run.
Running --apply again is harmless.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any
from urllib.parse import unquote, urlsplit

ROLE = "research_reader"
TEST_ROLE = re.compile(r"^research_reader_test_[0-9a-f]{8}$")
EXPECTED_HOST = "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"
EXPECTED_DATABASE = "ibkr_staging"

# Every silver table and view (infra/medallion/catalog.json, layer silver).
SILVER_TABLES = (
    "accounts", "benchmark_prices", "cash_snapshots", "cash_transactions", "corporate_actions",
    "derived_portfolio_cash_balances", "derived_portfolio_cash_ledger", "derived_portfolio_exclusions",
    "derived_portfolio_nav", "derived_portfolio_positions", "fx_rates", "fx_transactions",
    "instrument_classifications", "instruments", "market_data_prices", "nav_snapshots", "position_snapshots",
    "reference_fx_rates", "risk_free_rates", "synthetic_portfolio_components", "trades",
)
# Every silver view except subject_live_positions and subject_live_summary: those read the
# bronze live_* tables with the caller's rights, and the reader has no bronze access.
SILVER_VIEWS = (
    "subject_cash_activity_series", "subject_cash_balance_series", "subject_nav_series", "subject_position_series",
    "v_daily_nav", "v_latest_positions",
)
# U8 "as known on" history: granted when the migration that creates them has run.
HISTORY_TABLES = ("trades", "cash_transactions", "fx_transactions", "fx_rates", "nav_snapshots")
HISTORY_RELATIONS = tuple(f"{table}_history" for table in HISTORY_TABLES)
AS_KNOWN_FUNCTIONS = tuple(f"silver.{table}_as_known(timestamptz)" for table in HISTORY_TABLES)
READABLE = frozenset(SILVER_TABLES + SILVER_VIEWS + HISTORY_RELATIONS)
# The row-security policies on silver call these, so the reader must be able to run them.
ROW_SECURITY_FUNCTIONS = ("public.app_tenant_id()", "public.account_belongs_to_current_tenant(text)")
# Schemas the reader may use: silver for the data, public only for the row-security functions
# above (no table or view in public is granted).
SCHEMAS = ("silver", "public")
# Functions that run with their owner's rights and that every login can run. The reader may
# run these: silver.history_recording() only says whether U8 recording is on.
ALLOWED_DEFINER_FUNCTIONS = frozenset({"silver.history_recording()"})

ROLE_ATTRIBUTES = ("LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
                   "CONNECTION LIMIT 3")
ROLE_SETTINGS = (
    "SET search_path = silver, public",
    "SET default_transaction_read_only = on",
    "SET statement_timeout = '300s'",
    "SET idle_in_transaction_session_timeout = '120s'",
    "SET lock_timeout = '5s'",
)


def _role(role: str) -> str:
    # Deliberately not a general identifier escaper: only the reader (or a test copy) is accepted.
    if role != ROLE and not TEST_ROLE.match(role):
        raise ValueError("Unapproved research reader role")
    return role


def grant_statements(role: str = ROLE, database: str = EXPECTED_DATABASE) -> list[tuple[str, str | None]]:
    """(statement, condition) pairs; a condition names the object that must exist first."""
    role = _role(role)
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", database):
        raise ValueError("Unexpected database name")
    statements: list[tuple[str, str | None]] = [
        (f"GRANT CONNECT ON DATABASE {database} TO {role}", None),
        *((f"GRANT USAGE ON SCHEMA {schema} TO {role}", None) for schema in SCHEMAS),
        *((f"GRANT EXECUTE ON FUNCTION {function} TO {role}", None) for function in ROW_SECURITY_FUNCTIONS),
        *((f"GRANT SELECT ON TABLE silver.{name} TO {role}", None) for name in SILVER_TABLES + SILVER_VIEWS),
        *((f"GRANT SELECT ON TABLE silver.{name} TO {role}", f"silver.{name}") for name in HISTORY_RELATIONS),
        *((f"GRANT EXECUTE ON FUNCTION {function} TO {role}", function) for function in AS_KNOWN_FUNCTIONS),
    ]
    return statements


def setting_statements(role: str = ROLE) -> list[str]:
    role = _role(role)
    return [f"ALTER ROLE {role} {setting}" for setting in ROLE_SETTINGS]


def dry_run_sql(role: str = ROLE, database: str = EXPECTED_DATABASE) -> str:
    lines = ["BEGIN;",
             "-- Only if the role does not exist yet; the password comes from RESEARCH_LIVE_READER_URL.",
             f"CREATE ROLE {_role(role)} {ROLE_ATTRIBUTES} PASSWORD '<from RESEARCH_LIVE_READER_URL>';",
             *(f"{statement};" for statement in setting_statements(role))]
    for statement, condition in grant_statements(role, database):
        lines.append(f"{statement};" + (f"  -- when {condition} exists" if condition else ""))
    lines += ["-- then the privilege check (--check); any problem rolls the transaction back", "COMMIT;"]
    return "\n".join(lines)


def _exists(connection, name: str) -> bool:
    function = "to_regprocedure" if name.endswith(")") else "to_regclass"
    return connection.execute(f"SELECT {function}(%s) IS NOT NULL", (name,)).fetchone()[0]


def provision(connection, *, password: str, role: str = ROLE, database: str = EXPECTED_DATABASE) -> dict[str, Any]:
    """Create the login if missing and grant the list. Never alters an existing login."""
    from psycopg import sql

    role = _role(role)
    if len(password) < 24:
        raise ValueError("The research reader password must have at least 24 characters")
    existing = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is not None
    if not existing:
        connection.execute(sql.SQL("CREATE ROLE {} " + ROLE_ATTRIBUTES + " PASSWORD {}").format(
            sql.Identifier(role), sql.Literal(password)))
        for statement in setting_statements(role):
            connection.execute(statement)
    applied, waiting = [], []
    for statement, condition in grant_statements(role, database):
        if condition and not _exists(connection, condition):
            waiting.append(condition)
            continue
        connection.execute(statement)
        applied.append(statement)
    return {"role": role, "created": not existing, "grants": len(applied), "waiting_for": waiting}


def privilege_report(connection, role: str = ROLE) -> dict[str, Any]:
    """Every privilege the login has: relations it can read or change, schemas, functions it can
    run, database rights, attributes, memberships and settings."""
    role = _role(role)
    relations = connection.execute(
        """
        SELECT n.nspname, c.relname, c.relkind::text,
               has_table_privilege(%(r)s, c.oid, 'SELECT'),
               has_table_privilege(%(r)s, c.oid, 'INSERT') OR has_table_privilege(%(r)s, c.oid, 'UPDATE')
                 OR has_table_privilege(%(r)s, c.oid, 'DELETE') OR has_table_privilege(%(r)s, c.oid, 'TRUNCATE'),
               has_any_column_privilege(%(r)s, c.oid, 'SELECT')
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
         ORDER BY 1, 2
        """, {"r": role}).fetchall()
    readable = [f"{schema}.{name}" for schema, name, _, select, _, column in relations if select or column]
    writable = [f"{schema}.{name}" for schema, name, _, _, change, _ in relations if change]
    sequences = [f"{schema}.{name}" for schema, name in connection.execute(
        """
        SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind = 'S' AND (has_sequence_privilege(%(r)s, c.oid, 'USAGE')
               OR has_sequence_privilege(%(r)s, c.oid, 'UPDATE') OR has_sequence_privilege(%(r)s, c.oid, 'SELECT'))
         ORDER BY 1, 2
        """, {"r": role}).fetchall()]
    schemas = connection.execute(
        """
        SELECT nspname, has_schema_privilege(%(r)s, oid, 'USAGE'), has_schema_privilege(%(r)s, oid, 'CREATE')
          FROM pg_namespace WHERE nspname NOT IN ('pg_catalog', 'information_schema') AND nspname NOT LIKE 'pg_%%'
         ORDER BY 1
        """, {"r": role}).fetchall()
    usable = [name for name, usage, _ in schemas if usage]
    functions = connection.execute(
        """
        SELECT n.nspname || '.' || p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')', p.prosecdef
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE n.nspname = ANY(%(s)s) AND has_function_privilege(%(r)s, p.oid, 'EXECUTE')
           -- Trigger functions run only as triggers; nobody can call one directly.
           AND p.prorettype NOT IN ('trigger'::regtype, 'event_trigger'::regtype)
         ORDER BY 1
        """, {"r": role, "s": usable}).fetchall()
    database = connection.execute(
        "SELECT has_database_privilege(%(r)s, current_database(), 'CONNECT'), "
        "has_database_privilege(%(r)s, current_database(), 'CREATE'), "
        "has_database_privilege(%(r)s, current_database(), 'TEMPORARY')", {"r": role}).fetchone()
    attributes = connection.execute(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication, rolcanlogin, rolinherit, "
        "rolconnlimit FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
    members = [row[0] for row in connection.execute(
        "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid "
        "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = %s ORDER BY 1", (role,)).fetchall()]
    settings = [setting for row in connection.execute(
        "SELECT s.setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole WHERE r.rolname = %s",
        (role,)).fetchall() for setting in (row[0] or [])]
    return {
        "role": role,
        "attributes": None if attributes is None else dict(zip(
            ("superuser", "bypass_row_security", "create_role", "create_database", "replication", "login", "inherit",
             "connection_limit"), attributes)),
        "member_of": members,
        "settings": sorted(settings),
        "database": dict(zip(("connect", "create", "temporary"), database)),
        "schemas": {name: {"usage": usage, "create": create} for name, usage, create in schemas if usage or create},
        "readable": readable,
        "writable": writable,
        "sequences": sequences,
        "functions": [name for name, _ in functions],
        "definer_functions": [name for name, definer in functions if definer],
    }


def least_privilege_problems(report: dict[str, Any]) -> list[str]:
    problems = []
    attributes = report["attributes"]
    if attributes is None:
        return ["the login does not exist"]
    if any(attributes[key] for key in ("superuser", "bypass_row_security", "create_role", "create_database",
                                       "replication")):
        problems.append("the login has superuser, bypass-row-security, create or replication rights")
    if report["member_of"]:
        problems.append("the login is a member of other roles: " + ", ".join(report["member_of"]))
    if report["writable"]:
        problems.append("the login can change " + ", ".join(report["writable"]))
    if report["sequences"]:
        problems.append("the login can use sequences " + ", ".join(report["sequences"]))
    outside = [name for name in report["readable"]
               if not name.startswith("silver.") or name.split(".", 1)[1] not in READABLE]
    if outside:
        problems.append("the login can read more than silver: " + ", ".join(outside))
    extra = sorted(set(report["schemas"]) - set(SCHEMAS))
    if extra:
        problems.append("the login can use schemas " + ", ".join(extra))
    if any(item["create"] for item in report["schemas"].values()) or report["database"]["create"]:
        problems.append("the login can create objects")
    definers = sorted(set(report["definer_functions"]) - ALLOWED_DEFINER_FUNCTIONS)
    if definers:
        problems.append("the login can run functions with their owner's rights: " + ", ".join(definers))
    if "default_transaction_read_only=on" not in report["settings"]:
        problems.append("the login is not read-only by default")
    return problems


def _targets(environ) -> tuple[str, str]:
    admin_url = environ.get("DATABASE_ADMIN_URL", "")
    reader_url = environ.get("RESEARCH_LIVE_READER_URL", "")
    admin, reader = urlsplit(admin_url), urlsplit(reader_url)
    if (unquote(reader.username or "") != ROLE or reader.hostname != EXPECTED_HOST
            or reader.path != "/" + EXPECTED_DATABASE or admin.hostname != EXPECTED_HOST
            or admin.path != "/" + EXPECTED_DATABASE):
        raise RuntimeError("DATABASE_ADMIN_URL and RESEARCH_LIVE_READER_URL must both name the live server's "
                           f"{EXPECTED_DATABASE} database, the second as {ROLE}")
    return admin_url, unquote(reader.password or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The research_reader login on the live server.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print the SQL; connect to nothing")
    mode.add_argument("--apply", action="store_true", help="create the login and grants, then check them")
    mode.add_argument("--check", action="store_true", help="print every privilege the login has")
    args = parser.parse_args(argv)
    if args.dry_run:
        print(dry_run_sql())
        return 0

    import psycopg

    admin_url, password = _targets(os.environ)
    with psycopg.connect(admin_url, connect_timeout=20,
                         options="-c statement_timeout=60000 -c lock_timeout=5000") as connection:
        outcome = provision(connection, password=password) if args.apply else None
        report = privilege_report(connection)
        problems = least_privilege_problems(report)
        if args.apply and problems:
            connection.rollback()
            print("RESEARCH_READER " + json.dumps({"status": "fail", "problems": problems, "report": report}))
            return 1
    print("RESEARCH_READER " + json.dumps({"status": "pass" if not problems else "fail", "applied": outcome,
                                           "problems": problems, "report": report}, indent=1))
    return 0 if not problems else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - one line for Run Command's short output
        detail = {"status": "fail", "error_type": type(error).__name__}
        if type(error) in (RuntimeError, ValueError):  # this script's own messages carry no secret
            detail["message"] = str(error)
        print("RESEARCH_READER " + json.dumps(detail), flush=True)
        raise SystemExit(1) from None
