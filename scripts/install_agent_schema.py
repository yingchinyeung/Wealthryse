"""Install the agent schema and sign-in principals. Run on purpose, with approval.

Not part of the automatic migrations. Run it as the PostgreSQL administrator
that owns the platform tables, from a machine inside the private network
(for example the private admin VM), with a short-lived Entra token:

    python scripts/install_agent_schema.py --database-url "<admin URL>" --dry-run
    python scripts/install_agent_schema.py --database-url "<admin URL>" \
        --principal id-wr-agent-tools=reader --principal id-wr-agent=writer \
        --principal id-wr-agent-jobs=writer \
        --grant holdings_explainer=U1234567 --grant data_quality=U1234567

What it does, in one transaction:
  1. applies migrations/agent/001_agent_schema.sql (idempotent);
  2. creates each managed identity's Entra login role if missing
     (pgaadauth_create_principal) and grants it agent_reader or agent_writer;
  3. records which accounts each scheduled job may cover.
Nothing in the public schema is changed. --dry-run rolls everything back.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "migrations" / "agent" / "001_agent_schema.sql"
JOBS = {"holdings_explainer", "month_end_drafter", "data_quality"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z")


def _pair(value: str) -> tuple[str, str]:
    left, sep, right = value.partition("=")
    if not sep or not NAME.match(left) or not NAME.match(right):
        raise argparse.ArgumentTypeError(f"bad value: {value}")
    return left, right


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--tenant-id", default="00000000-0000-0000-0000-000000000001")
    parser.add_argument("--principal", action="append", default=[], type=_pair,
                        help="identity-name=reader|writer")
    parser.add_argument("--grant", action="append", default=[], type=_pair,
                        help="job=account_id")
    parser.add_argument("--granted-by", default="install_agent_schema")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    import psycopg
    from psycopg import sql

    for name, role in args.principal:
        if role not in ("reader", "writer"):
            parser.error(f"{name}: role must be reader or writer")
    for job, _ in args.grant:
        if job not in JOBS:
            parser.error(f"unknown job {job}")

    with psycopg.connect(args.database_url, options=f"-c app.tenant_id={args.tenant_id}") as connection:
        connection.execute(SCHEMA.read_text(encoding="utf-8"))
        for name, role in args.principal:
            exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
            if not exists:
                connection.execute("SELECT * FROM pgaadauth_create_principal(%s, false, false)", (name,))
            connection.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(f"agent_{role}"), sql.Identifier(name)))
            connection.execute(sql.SQL("ALTER ROLE {} SET search_path = agent").format(sql.Identifier(name)))
            print(f"{name}: agent_{role}{'' if exists else ' (login created)'}")
        for job, account in args.grant:
            found = connection.execute(
                "SELECT 1 FROM public.platform_subjects WHERE tenant_id = %s AND subject_id = %s AND lifecycle_status = 'active'",
                (args.tenant_id, account)).fetchone()
            if not found:
                raise SystemExit(f"{account} is not an active account; nothing was changed")
            connection.execute(
                "INSERT INTO agent.service_grants(tenant_id, job_name, subject_id, granted_by) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING", (args.tenant_id, job, account, args.granted_by))
            print(f"{job}: {account}")
        if args.dry_run:
            connection.rollback()
            print("Dry run: rolled back.")
        else:
            connection.commit()
            print("Installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
