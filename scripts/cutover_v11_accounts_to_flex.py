"""Move the v1.1-fed accounts onto v2's own daily Flex pull, or move them back.

Dry run by default: it checks and prints the plan and writes nothing. --apply
writes the cutover in one transaction with an audit event; --revert restores
the existing_v11 registrations the cutover closed. Both are safe to re-run.

  DATABASE_URL            the approval login is enough (no DELETE is used)
  PORTFOLIO_TENANT_ID     optional, defaults to the platform tenant
  CUTOVER_FLEX_QUERY_ID   v1.1's Flex query ID (digits; never printed)

  python scripts/cutover_v11_accounts_to_flex.py --effective-date 2026-10-20 \
      --token-secret-name ibkr-flex-source-<32 hex> --statement v11.xml \
      [--account U...] [--token-fingerprint <16 hex>] [--approved-by <identity>] [--apply]
  python scripts/cutover_v11_accounts_to_flex.py --revert --effective-date 2026-10-20 \
      [--account U...] --approved-by <identity> [--apply]

The effective date is the first data date the Flex pull owns. With
INSTITUTIONAL_DISPATCH_RUN_DATE=previous_hkt_day, a switch on day D uses D - 1.
No IBKR request is made; the statement is one v1.1 already downloaded.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from psycopg.rows import dict_row


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import DEFAULT_TENANT_ID  # noqa: E402
from ibkr_etl.flex_cutover import (  # noqa: E402
    CutoverRefused,
    CutoverRequest,
    apply_cutover,
    apply_revert,
    identity_sha256,
    plan_cutover,
    plan_revert,
)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--effective-date", required=True, type=date.fromisoformat)
    parser.add_argument("--account", action="append", default=[],
                        help="subject to move; repeat. Default: what account-sync imports today")
    parser.add_argument("--token-secret-name", help="the token's name in v2's flex vault")
    parser.add_argument("--query-id-env", default="CUTOVER_FLEX_QUERY_ID",
                        help="environment variable that holds the query ID")
    parser.add_argument("--token-fingerprint", help="first 16 hex of the token's SHA-256 (optional)")
    parser.add_argument("--statement", type=Path, help="one v1.1 Flex statement (XML)")
    parser.add_argument("--approved-by", help="approver's Entra object ID or email")
    parser.add_argument("--revert", action="store_true")
    parser.add_argument("--apply", action="store_true", help="write; without it nothing changes")
    arguments = parser.parse_args(argv)
    if not arguments.revert and (not arguments.token_secret_name or not arguments.statement):
        parser.error("--token-secret-name and --statement are required for the cutover")
    if arguments.apply and not arguments.approved_by:
        parser.error("--approved-by is required with --apply")
    return arguments


def main(argv: list[str] | None = None) -> int:
    import psycopg

    arguments = _arguments(argv)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL is required")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    today = datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
    approver = identity_sha256(arguments.approved_by) if arguments.approved_by else None
    accounts = tuple(arguments.account)
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        try:
            if arguments.revert:
                if arguments.apply:
                    result = apply_revert(
                        connection, tenant_id=tenant_id,
                        effective_date=arguments.effective_date,
                        approver_sha256=approver, subject_ids=accounts,
                    )
                else:
                    result = plan_revert(
                        connection, tenant_id=tenant_id,
                        effective_date=arguments.effective_date, subject_ids=accounts,
                    )
            else:
                request = CutoverRequest(
                    tenant_id=tenant_id,
                    effective_date=arguments.effective_date,
                    token_secret_name=arguments.token_secret_name.strip(),
                    query_id=os.environ.get(arguments.query_id_env, "").strip(),
                    statement=arguments.statement.read_bytes(),
                    statement_name=arguments.statement.name,
                    approver_sha256=approver,
                    subject_ids=accounts,
                    token_fingerprint=arguments.token_fingerprint,
                )
                if arguments.apply:
                    result = apply_cutover(connection, request, today=today)
                else:
                    result = plan_cutover(connection, request, today=today)
        except CutoverRefused as refused:
            connection.rollback()
            print(json.dumps(refused.plan, sort_keys=True, default=str))
            return 2
        if arguments.apply:
            connection.commit()
        else:
            connection.rollback()
    result["mode"] = "apply" if arguments.apply else "dry_run"
    print(json.dumps(result, sort_keys=True, default=str))
    return 2 if result["status"] == "refused" else 0


if __name__ == "__main__":
    raise SystemExit(main())
