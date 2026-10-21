"""Put a Kiwoom account's stored stock names and movement labels into English.

New collections are already written in English; this rewrites what an account
collected before that. It touches instruments.description,
cash_transactions.description and trades.order_reference only, and only
where the text is not English. Figures, keys and dates are unchanged.

    python scripts/translate_kiwoom_text.py KIWOOM-5057355911 [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import connect
from ibkr_etl.kiwoom_english import translate_account


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("account_id")
    parser.add_argument("--dry-run", action="store_true", help="count what would change, then roll back")
    args = parser.parse_args(argv)
    account_id = args.account_id.strip()
    if not account_id.startswith("KIWOOM-"):
        raise SystemExit("Only Kiwoom accounts (KIWOOM-...) are translated")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise SystemExit("DATABASE_URL must be a PostgreSQL connection URL")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        counts = translate_account(connection, account_id)
        if args.dry_run:
            connection.rollback()
        else:
            connection.commit()
    finally:
        connection.close()
    print(json.dumps({"account_id": account_id, "dry_run": args.dry_run, "changed": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
