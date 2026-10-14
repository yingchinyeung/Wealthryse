"""Stage 1 of the live IBKR data rollout, run inside one bootstrap job execution.

scripts/rollout_live_data_stage1.py sends this file's source as the execution's
command, followed by a BUNDLE of: the checksum of every PostgreSQL migration on
main, the text of migration 063, the live collector grant module, and the role
names. It needs only the standard library and psycopg, which the bootstrap
image already has, so no image is built.

It refuses, changing nothing, unless every migration the database has applied
matches main's checksum and 063 is the only one pending. Then, in separate
transactions, it applies 063 and records it exactly as bootstrap_postgres.py
does, and creates the ibkr_live_collector login with its grants, rolled back
unless the grants are least-privilege. The login's password arrives as a
SCRAM-SHA-256 verifier (LIVE_COLLECTOR_VERIFIER), never as the password. A
rerun applies nothing twice and leaves an existing login as it is. It prints one
LIVE_DATA_STAGE1 line of JSON with no secret in it.

Environment: DATABASE_ADMIN_URL, LIVE_COLLECTOR_VERIFIER.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import sys
import types
from datetime import datetime, timezone

PREFIX = "LIVE_DATA_STAGE1 "
BUNDLE = None  # replaced by rollout_live_data_stage1.py
VERIFIER = re.compile(r"SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]{24}\$[A-Za-z0-9+/=]{44}:[A-Za-z0-9+/=]{44}")
VIEWS = ("subject_live_summary", "subject_live_positions")


def decode(encoded: str) -> dict:
    return json.loads(gzip.decompress(base64.b64decode(encoded)))


def load_access(source: str):
    module = types.ModuleType("live_collector_access")
    exec(compile(source, "live_collector_access", "exec"), module.__dict__)
    return module


def run(connection, bundle: dict, verifier: str) -> dict:
    """Apply the target migration and create the collector login on an autocommit connection."""
    target = bundle["target"]
    target_sql = bundle["target_sql"]
    expected = bundle["expected"]
    if hashlib.sha256(target_sql.encode("utf-8")).hexdigest() != expected.get(target):
        raise ValueError("Migration text does not match its checksum")
    if not VERIFIER.fullmatch(verifier):
        raise ValueError("LIVE_COLLECTOR_VERIFIER is not a SCRAM-SHA-256 verifier")
    access = load_access(bundle["access_source"])
    role, web_role, database = bundle["role"], bundle["web_role"], bundle["database"]
    if connection.info.dbname != database:
        raise ValueError("Connected to an unexpected database")
    access.grant_statements(role)

    applied = dict(connection.execute("SELECT version, sha256 FROM schema_migrations").fetchall())
    changed = sorted(name for name, digest in applied.items() if name in expected and expected[name] != digest)
    unknown = sorted(name for name in applied if name not in expected)
    pending = sorted(name for name in expected if name not in applied)
    record = {"target": target, "changed": changed, "not_on_main": unknown, "pending": pending}
    if changed or unknown or pending not in ([], [target]):
        return dict(record, status="refused", reason="The database's migrations do not match main")

    if pending:
        with connection.transaction():
            connection.execute(target_sql)
            connection.execute(
                "INSERT INTO schema_migrations(version, sha256, applied_at) VALUES (%s, %s, %s)",
                (target, expected[target], datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )

    role_existed = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is not None
    with connection.transaction():
        if not role_existed:
            access.provision(connection, password=verifier, database=database, role=role)
        problems = access.least_privilege_problems(access.privilege_report(connection, role))
        if problems and not role_existed:
            # Leaves the transaction, so the new login and its grants are rolled back.
            raise RuntimeError("Live collector grants are not least-privilege: " + "; ".join(problems))

    if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (web_role,)).fetchone() is None:
        web_unreadable = ["role " + web_role + " is missing"]
    else:
        web_unreadable = [
            name for name in access.LIVE_TABLES + VIEWS
            if not connection.execute("SELECT has_table_privilege(%s, %s, 'SELECT')",
                                      (web_role, "public." + name)).fetchone()[0]
        ]
    passed = not problems and not web_unreadable
    return dict(
        record,
        status="pass" if passed else "fail",
        migration_applied=bool(pending),
        migration_sha256=expected[target],
        migrations_matched=len(applied),
        role=role,
        role_created=not role_existed,
        role_problems=problems,
        website_role=web_role,
        website_cannot_read=web_unreadable,
    )


def main() -> int:
    import psycopg

    try:
        with psycopg.connect(os.environ["DATABASE_ADMIN_URL"], autocommit=True, connect_timeout=20,
                             options="-c statement_timeout=120000 -c lock_timeout=10000") as connection:
            record = run(connection, decode(BUNDLE), os.environ["LIVE_COLLECTOR_VERIFIER"])
    except Exception as error:
        # The type and message only; psycopg messages carry no password.
        record = {"status": "error", "error_type": type(error).__name__, "error": str(error)[:500]}
    print(PREFIX + json.dumps(record, sort_keys=True), flush=True)
    return 0 if record["status"] == "pass" else 1
