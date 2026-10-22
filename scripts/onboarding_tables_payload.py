"""The new account onboarding tables, run inside one bootstrap job execution.

scripts/rollout_onboarding_tables.py sends this file's source as the execution's
command, followed by a BUNDLE of: the checksum of every PostgreSQL migration on
main, the text of migrations 064 and 065, and the role names. It needs only the
standard library and psycopg, which the bootstrap image already has, so no image
is built.

It refuses, changing nothing, unless every migration the database has applied
matches main's checksum and nothing is pending except 064, 065 and the
migrations the bundle lists as held back or left to another rollout (neither
064 nor 065 depends on them). Then, each in its own transaction, it applies
064 and 065 and records them exactly as bootstrap_postgres.py does. Last, in one
transaction, it grants the website what the onboarding pages need, as
bootstrap_postgres.py now does too: reads for the website role, and reads and
writes (no deletes; the event history only reads and appends) for the approval
role the pages write through. A rerun applies nothing twice. It prints one
ONBOARDING_TABLES line of JSON.

Environment: DATABASE_ADMIN_URL.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
from datetime import datetime, timezone

PREFIX = "ONBOARDING_TABLES "
BUNDLE = None  # replaced by rollout_onboarding_tables.py
# What the onboarding pages write through the approval role; the event history is append-only.
WRITABLE = ("broker_connections", "broker_connection_accounts", "broker_onboarding_cases",
            "broker_onboarding_checks", "broker_onboarding_approvals")
APPEND_ONLY = ("broker_onboarding_events",)
READABLE = WRITABLE + APPEND_ONLY + ("security_master", "security_identifiers")


def decode(encoded: str) -> dict:
    return json.loads(gzip.decompress(base64.b64decode(encoded)))


def grant_statements(web_role: str, approval_role: str):
    from psycopg import sql

    names = lambda tables: sql.SQL(", ").join(sql.Identifier(t) for t in tables)
    return [
        sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(names(READABLE), sql.Identifier(web_role)),
        sql.SQL("GRANT SELECT, INSERT, UPDATE ON TABLE {} TO {}").format(names(WRITABLE), sql.Identifier(approval_role)),
        sql.SQL("GRANT SELECT, INSERT ON TABLE {} TO {}").format(names(APPEND_ONLY), sql.Identifier(approval_role)),
    ]


def privilege_problems(connection, web_role: str, approval_role: str) -> list[str]:
    def has(role, table, privilege):
        return connection.execute("SELECT has_table_privilege(%s, %s, %s)",
                                  (role, "public." + table, privilege)).fetchone()[0]

    problems = [f"{web_role} cannot read {t}" for t in READABLE if not has(web_role, t, "SELECT")]
    problems += [f"{web_role} can {p.lower()} {t}" for t in READABLE for p in ("INSERT", "UPDATE", "DELETE")
                 if has(web_role, t, p)]
    problems += [f"{approval_role} cannot {p.lower()} {t}" for t in WRITABLE for p in ("SELECT", "INSERT", "UPDATE")
                 if not has(approval_role, t, p)]
    problems += [f"{approval_role} cannot {p.lower()} {t}" for t in APPEND_ONLY for p in ("SELECT", "INSERT")
                 if not has(approval_role, t, p)]
    problems += [f"{approval_role} can delete {t}" for t in WRITABLE + APPEND_ONLY if has(approval_role, t, "DELETE")]
    problems += [f"{approval_role} can update {t}" for t in APPEND_ONLY if has(approval_role, t, "UPDATE")]
    return problems


def run(connection, bundle: dict) -> dict:
    """Apply the target migrations and the website grants on an autocommit connection."""
    targets, texts, expected = bundle["targets"], bundle["target_sql"], bundle["expected"]
    for name in targets:
        if hashlib.sha256(texts[name].encode("utf-8")).hexdigest() != expected.get(name):
            raise ValueError("Migration text does not match its checksum: " + name)
    web_role, approval_role = bundle["web_role"], bundle["approval_role"]
    if connection.info.dbname != bundle["database"]:
        raise ValueError("Connected to an unexpected database")

    applied = dict(connection.execute("SELECT version, sha256 FROM schema_migrations").fetchall())
    changed = sorted(name for name, digest in applied.items() if name in expected and expected[name] != digest)
    unknown = sorted(name for name in applied if name not in expected)
    pending = sorted(name for name in expected if name not in applied)
    allowed = set(bundle.get("held_back", ())) | set(bundle.get("other_rollouts", ()))
    unexpected = [name for name in pending if name not in targets and name not in allowed]
    record = {"targets": targets, "changed": changed, "not_on_main": unknown, "pending": pending}
    if changed or unknown or unexpected:
        return dict(record, status="refused", reason="The database's migrations do not match main")
    missing_roles = [role for role in (web_role, approval_role)
                     if connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone() is None]
    if missing_roles:
        return dict(record, status="refused", reason="Missing roles: " + ", ".join(missing_roles))

    applied_now = []
    for name in targets:
        if name not in pending:
            continue
        with connection.transaction():
            connection.execute(texts[name])
            connection.execute(
                "INSERT INTO schema_migrations(version, sha256, applied_at) VALUES (%s, %s, %s)",
                (name, expected[name], datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
        applied_now.append(name)

    with connection.transaction():
        for statement in grant_statements(web_role, approval_role):
            connection.execute(statement)
    problems = privilege_problems(connection, web_role, approval_role)
    return dict(record, status="fail" if problems else "pass", applied=applied_now,
                migration_sha256={name: expected[name] for name in targets},
                migrations_matched=len(applied), website_roles=[web_role, approval_role], privilege_problems=problems)


def main() -> int:
    try:
        import psycopg

        with psycopg.connect(os.environ["DATABASE_ADMIN_URL"], autocommit=True, connect_timeout=20,
                             options="-c statement_timeout=120000 -c lock_timeout=10000") as connection:
            record = run(connection, decode(BUNDLE))
    except Exception as error:
        # The type and message only; psycopg messages carry no password.
        record = {"status": "error", "error_type": type(error).__name__, "error": str(error)[:500]}
    print(PREFIX + json.dumps(record, sort_keys=True), flush=True)
    return 0 if record["status"] == "pass" else 1
