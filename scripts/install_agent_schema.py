"""Install the agent schema and sign-in principals. Run on purpose, with approval.

Not part of the automatic migrations. Run it as the PostgreSQL administrator
that owns the platform tables, from a machine inside the private network
(for example the private admin VM), with a short-lived Entra token:

    python scripts/install_agent_schema.py --database-url "<admin URL>" --dry-run
    python scripts/install_agent_schema.py --database-url "<admin URL>" \
        --principal id-wr-agent-tools=reader --principal id-wr-agent=writer \
        --principal id-wr-agent-jobs=writer \
        --grant holdings_explainer=U1234567 --grant data_quality=U1234567 \
        --platform-reader ibkr_stg_app

What it does, in one transaction:
  1. applies every file in migrations/agent in name order (each idempotent);
  2. creates each managed identity's Entra login role if missing
     (pgaadauth_create_principal) and grants it agent_reader or agent_writer;
  3. records which accounts each scheduled job may cover;
  4. lets the platform's job role read the onboarding reviews, so the
     month-end report job can record them on their cases (--platform-reader).
Nothing in the public schema is changed. --dry-run rolls everything back.

On a server where the Entra administrator does not own the platform tables,
run it in two parts. First, signed in as the Entra administrator, create the
logins only:

    python scripts/install_agent_schema.py --database-url "<Entra admin URL>" --logins-only \
        --principal id-wr-agent-tools=reader --principal id-wr-agent=writer \
        --principal id-wr-agent-jobs=writer

Then, signed in as the table owner, install the schema and grants for the
logins that now exist:

    python scripts/install_agent_schema.py --database-url "<owner URL>" --existing-logins \
        --principal ... --grant ...
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = tuple(sorted((ROOT / "migrations" / "agent").glob("*.sql")))
JOBS = {"holdings_explainer", "month_end_drafter", "data_quality", "month_team", "onboarding"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}\Z")


def _pair(value: str) -> tuple[str, str]:
    left, sep, right = value.partition("=")
    if not sep or not NAME.match(left) or not NAME.match(right):
        raise argparse.ArgumentTypeError(f"bad value: {value}")
    return left, right


def _role_name(value: str) -> str:
    if not NAME.match(value):
        raise argparse.ArgumentTypeError(f"bad role name: {value}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--tenant-id", default="00000000-0000-0000-0000-000000000001")
    parser.add_argument("--principal", action="append", default=[], type=_pair,
                        help="identity-name=reader|writer")
    parser.add_argument("--grant", action="append", default=[], type=_pair,
                        help="job=account_id")
    parser.add_argument("--granted-by", default="install_agent_schema")
    parser.add_argument("--platform-reader", type=_role_name,
                        help="the platform jobs' database role, which may read agent.onboarding_reviews")
    parser.add_argument("--dry-run", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--logins-only", action="store_true",
                      help="only create the Entra logins and set their search_path (run as the Entra administrator)")
    mode.add_argument("--existing-logins", action="store_true",
                      help="do not create logins; each must already exist (run as the table owner)")
    args = parser.parse_args(argv)

    import psycopg
    from psycopg import sql

    for name, role in args.principal:
        if role not in ("reader", "writer"):
            parser.error(f"{name}: role must be reader or writer")
    if args.logins_only and (args.grant or args.platform_reader):
        parser.error("--logins-only does not take --grant or --platform-reader")
    for job, _ in args.grant:
        if job not in JOBS:
            parser.error(f"unknown job {job}")

    with psycopg.connect(args.database_url, options=f"-c app.tenant_id={args.tenant_id}") as connection:
        if not args.logins_only:
            for schema in SCHEMAS:
                connection.execute(schema.read_text(encoding="utf-8"))
        for name, role in args.principal:
            exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,)).fetchone()
            if not exists:
                if args.existing_logins:
                    raise SystemExit(f"{name} has no login yet; run --logins-only first. Nothing was changed")
                connection.execute("SELECT * FROM pgaadauth_create_principal(%s, false, false)", (name,))
            if not args.existing_logins:
                connection.execute(sql.SQL("ALTER ROLE {} SET search_path = agent").format(sql.Identifier(name)))
            if args.logins_only:
                print(f"{name}: login {'exists' if exists else 'created'}")
                continue
            connection.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(f"agent_{role}"), sql.Identifier(name)))
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
        if args.platform_reader:
            if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (args.platform_reader,)).fetchone():
                raise SystemExit(f"{args.platform_reader} is not a database role; nothing was changed")
            reader = sql.Identifier(args.platform_reader)
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA agent TO {}").format(reader))
            connection.execute(sql.SQL("GRANT SELECT ON agent.onboarding_reviews TO {}").format(reader))
            print(f"{args.platform_reader}: reads agent.onboarding_reviews")
        if args.dry_run:
            connection.rollback()
            print("Dry run: rolled back.")
        else:
            connection.commit()
            print("Installed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
