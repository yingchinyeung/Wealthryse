"""The research server's schema and roles: research, its owner, and the job's login.

The research server (infra/research.bicep) holds only the ``research`` schema. Its tables
are an index of what the research job did: one row per run (research.job_runs) and per
feature export (research.feature_exports). The data itself is in the research-* containers.

* ``research_owner`` (no login) owns the schema and its tables.
* ``research_job`` (login) can read and add rows in research.* and nothing else: it cannot
  create objects, cannot use other schemas' tables, and has row security by tenant
  (app.tenant_id), like the platform's logins.

  python scripts/research/research_server_roles.py --dry-run   prints the SQL; connects to nothing
  python scripts/research/research_server_roles.py --apply     creates the schema, tables and roles
  python scripts/research/research_server_roles.py --check     prints every privilege the job login has

Run --apply and --check from the private admin VM with RESEARCH_DATABASE_ADMIN_URL (Key
Vault research-db-admin-url) and RESEARCH_DATABASE_URL (Key Vault research-db-url, which
supplies the job login's password, so it is never typed or printed). The server must be
running: ``python scripts/research/run_research_job.py start-server`` or the portal, and stop
it again afterwards. --apply runs in one transaction and is safe to repeat: an existing
login keeps its password, and any problem the check finds rolls everything back.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Any
from urllib.parse import unquote, urlsplit

SCHEMA = "research"
OWNER = "research_owner"
JOB = "research_job"
TEST_ROLE = re.compile(r"^research_(owner|job)_test_[0-9a-f]{8}$")
EXPECTED_HOST = "ibkrv2stg-research-pg.postgres.database.azure.com"
EXPECTED_DATABASE = "research"

JOB_ATTRIBUTES = ("LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
                  "CONNECTION LIMIT 4")
JOB_SETTINGS = ("SET search_path = research", "SET statement_timeout = '600s'",
                "SET idle_in_transaction_session_timeout = '120s'")
# The job's rights on each table: it adds runs and exports and updates a run's status; it
# never deletes.
JOB_TABLES = {"job_runs": "SELECT, INSERT, UPDATE", "feature_exports": "SELECT, INSERT"}

TABLE_DDL = (
    """CREATE TABLE IF NOT EXISTS research.job_runs (
        run_id TEXT PRIMARY KEY,
        tenant_id UUID NOT NULL,
        command TEXT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        finished_at TIMESTAMPTZ,
        status TEXT NOT NULL CHECK (status IN ('running', 'passed', 'failed')),
        code_version TEXT NOT NULL,
        detail JSONB NOT NULL DEFAULT '{}'::jsonb
    )""",
    """CREATE TABLE IF NOT EXISTS research.feature_exports (
        tenant_id UUID NOT NULL,
        dataset TEXT NOT NULL,
        as_known_on DATE NOT NULL,
        subjects TEXT NOT NULL,
        manifest_path TEXT NOT NULL,
        rows_sha256 TEXT NOT NULL CHECK (rows_sha256 ~ '^[0-9a-f]{64}$'),
        row_count BIGINT NOT NULL CHECK (row_count >= 0),
        run_id TEXT NOT NULL REFERENCES research.job_runs(run_id),
        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (tenant_id, dataset, as_known_on, subjects, rows_sha256)
    )""",
)
TENANT_RULE = "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"


def _roles(owner: str, job: str) -> tuple[str, str]:
    # Deliberately not a general identifier escaper: only these roles (or test copies) are accepted.
    for role, expected in ((owner, OWNER), (job, JOB)):
        if role != expected and not TEST_ROLE.match(role):
            raise ValueError("Unapproved research server role")
    return owner, job


def schema_statements(owner: str = OWNER, job: str = JOB, database: str = EXPECTED_DATABASE) -> list[str]:
    """Everything after the roles exist, run as the server's administrator."""
    owner, job = _roles(owner, job)
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", database):
        raise ValueError("Unexpected database name")
    statements = [
        f"REVOKE ALL ON DATABASE {database} FROM PUBLIC",
        f"GRANT CONNECT ON DATABASE {database} TO {job}",
        "REVOKE CREATE ON SCHEMA public FROM PUBLIC",
        f"CREATE SCHEMA IF NOT EXISTS {SCHEMA} AUTHORIZATION {owner}",
        # The administrator does not inherit the owner's rights, so it acts as the owner from here.
        f"SET LOCAL ROLE {owner}",
        f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC",
        f"GRANT USAGE ON SCHEMA {SCHEMA} TO {job}",
        *TABLE_DDL,
    ]
    for table, rights in JOB_TABLES.items():
        statements += [
            f"ALTER TABLE research.{table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE research.{table} FORCE ROW LEVEL SECURITY",
            f"DROP POLICY IF EXISTS tenant_isolation ON research.{table}",
            f"CREATE POLICY tenant_isolation ON research.{table} USING ({TENANT_RULE}) WITH CHECK ({TENANT_RULE})",
            f"REVOKE ALL ON TABLE research.{table} FROM PUBLIC",
            f"GRANT {rights} ON TABLE research.{table} TO {job}",
        ]
    statements += ["RESET ROLE"]
    return statements


def dry_run_sql(owner: str = OWNER, job: str = JOB, database: str = EXPECTED_DATABASE) -> str:
    owner, job = _roles(owner, job)
    lines = ["BEGIN;",
             "-- Each role only if it does not exist yet; the job's password comes from RESEARCH_DATABASE_URL.",
             f"CREATE ROLE {owner} NOLOGIN NOINHERIT;",
             f"GRANT {owner} TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;  -- so the administrator can act as owner",
             f"CREATE ROLE {job} {JOB_ATTRIBUTES} PASSWORD '<from RESEARCH_DATABASE_URL>';",
             *(f"ALTER ROLE {job} {setting};" for setting in JOB_SETTINGS),
             *(f"{statement};" for statement in schema_statements(owner, job, database)),
             "-- then the privilege check (--check); any problem rolls the transaction back",
             "COMMIT;"]
    return "\n".join(lines)


def provision(connection, *, password: str, owner: str = OWNER, job: str = JOB,
              database: str = EXPECTED_DATABASE) -> dict[str, Any]:
    from psycopg import sql

    owner, job = _roles(owner, job)
    if len(password) < 24:
        raise ValueError("The research job password must have at least 24 characters")
    created = []
    if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (owner,)).fetchone() is None:
        connection.execute(sql.SQL("CREATE ROLE {} NOLOGIN NOINHERIT").format(sql.Identifier(owner)))
        created.append(owner)
    member = connection.execute(
        "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid "
        "WHERE r.rolname = %s AND m.member = (SELECT oid FROM pg_roles WHERE rolname = current_user) AND m.set_option",
        (owner,)).fetchone()
    if member is None:
        connection.execute(sql.SQL("GRANT {} TO CURRENT_USER WITH INHERIT FALSE, SET TRUE").format(sql.Identifier(owner)))
    if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (job,)).fetchone() is None:
        connection.execute(sql.SQL("CREATE ROLE {} " + JOB_ATTRIBUTES + " PASSWORD {}").format(
            sql.Identifier(job), sql.Literal(password)))
        for setting in JOB_SETTINGS:
            connection.execute(f"ALTER ROLE {job} {setting}")
        created.append(job)
    for statement in schema_statements(owner, job, database):
        connection.execute(statement)
    return {"created": created, "schema": SCHEMA, "tables": sorted(JOB_TABLES)}


def privilege_report(connection, job: str = JOB) -> dict[str, Any]:
    """Every privilege the job login has on this server."""
    _roles(OWNER, job)
    relations = connection.execute(
        """
        SELECT n.nspname || '.' || c.relname,
               has_table_privilege(%(r)s, c.oid, 'SELECT') OR has_any_column_privilege(%(r)s, c.oid, 'SELECT'),
               array_remove(ARRAY[
                   CASE WHEN has_table_privilege(%(r)s, c.oid, 'INSERT') THEN 'INSERT' END,
                   CASE WHEN has_table_privilege(%(r)s, c.oid, 'UPDATE') THEN 'UPDATE' END,
                   CASE WHEN has_table_privilege(%(r)s, c.oid, 'DELETE') THEN 'DELETE' END,
                   CASE WHEN has_table_privilege(%(r)s, c.oid, 'TRUNCATE') THEN 'TRUNCATE' END], NULL)
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
         ORDER BY 1
        """, {"r": job}).fetchall()
    schemas = connection.execute(
        """
        SELECT nspname, has_schema_privilege(%(r)s, oid, 'USAGE'), has_schema_privilege(%(r)s, oid, 'CREATE')
          FROM pg_namespace WHERE nspname NOT IN ('pg_catalog', 'information_schema') AND nspname NOT LIKE 'pg_%%'
         ORDER BY 1
        """, {"r": job}).fetchall()
    database = connection.execute(
        "SELECT has_database_privilege(%(r)s, current_database(), 'CONNECT'), "
        "has_database_privilege(%(r)s, current_database(), 'CREATE')", {"r": job}).fetchone()
    attributes = connection.execute(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolreplication FROM pg_roles WHERE rolname = %s",
        (job,)).fetchone()
    members = [row[0] for row in connection.execute(
        "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid "
        "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = %s ORDER BY 1", (job,)).fetchall()]
    return {
        "role": job,
        "privileged_attributes": None if attributes is None else any(attributes),
        "member_of": members,
        "database": {"connect": database[0], "create": database[1]},
        "schemas": {name: {"usage": usage, "create": create} for name, usage, create in schemas if usage or create},
        "readable": [name for name, select, _ in relations if select],
        "writable": {name: list(rights) for name, _, rights in relations if rights},
    }


def least_privilege_problems(report: dict[str, Any]) -> list[str]:
    if report["privileged_attributes"] is None:
        return ["the job login does not exist"]
    problems = []
    if report["privileged_attributes"]:
        problems.append("the job login has superuser, bypass-row-security, create or replication rights")
    if report["member_of"]:
        problems.append("the job login is a member of other roles: " + ", ".join(report["member_of"]))
    expected = {f"research.{table}": sorted(rights.replace("SELECT, ", "").replace("SELECT", "").split(", "))
                for table, rights in JOB_TABLES.items()}
    expected = {name: [right for right in rights if right] for name, rights in expected.items()}
    actual = {name: sorted(rights) for name, rights in report["writable"].items()}
    if actual != expected:
        problems.append(f"the job login can change {actual}, expected {expected}")
    outside = [name for name in report["readable"] if not name.startswith("research.")]
    if outside:
        problems.append("the job login can read outside research: " + ", ".join(outside))
    if any(item["create"] for item in report["schemas"].values()) or report["database"]["create"]:
        problems.append("the job login can create objects")
    return problems


def _targets(environ) -> tuple[str, str]:
    admin_url = environ.get("RESEARCH_DATABASE_ADMIN_URL", "")
    job_url = environ.get("RESEARCH_DATABASE_URL", "")
    admin, job = urlsplit(admin_url), urlsplit(job_url)
    if (unquote(job.username or "") != JOB or job.hostname != EXPECTED_HOST or job.path != "/" + EXPECTED_DATABASE
            or admin.hostname != EXPECTED_HOST or admin.path != "/" + EXPECTED_DATABASE):
        raise RuntimeError("RESEARCH_DATABASE_ADMIN_URL and RESEARCH_DATABASE_URL must both name the research server's "
                           f"{EXPECTED_DATABASE} database, the second as {JOB}")
    return admin_url, unquote(job.password or "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The research server's schema and roles.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="print the SQL; connect to nothing")
    mode.add_argument("--apply", action="store_true", help="create the schema, tables and roles, then check them")
    mode.add_argument("--check", action="store_true", help="print every privilege the job login has")
    args = parser.parse_args(argv)
    if args.dry_run:
        print(dry_run_sql())
        return 0

    import psycopg

    admin_url, password = _targets(os.environ)
    with psycopg.connect(admin_url, connect_timeout=30,
                         options="-c statement_timeout=60000 -c lock_timeout=5000") as connection:
        outcome = provision(connection, password=password) if args.apply else None
        report = privilege_report(connection)
        problems = least_privilege_problems(report)
        if args.apply and problems:
            connection.rollback()
            print("RESEARCH_SERVER_ROLES " + json.dumps({"status": "fail", "problems": problems, "report": report}))
            return 1
    print("RESEARCH_SERVER_ROLES " + json.dumps({"status": "pass" if not problems else "fail", "applied": outcome,
                                                 "problems": problems, "report": report}, indent=1))
    return 0 if not problems else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:  # noqa: BLE001 - one line for Run Command's short output
        detail = {"status": "fail", "error_type": type(error).__name__}
        if type(error) in (RuntimeError, ValueError):  # this script's own messages carry no secret
            detail["message"] = str(error)
        print("RESEARCH_SERVER_ROLES " + json.dumps(detail), flush=True)
        raise SystemExit(1) from None
