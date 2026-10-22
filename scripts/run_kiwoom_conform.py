"""Check Kiwoom's silver rows against the answers stored in bronze (ibkr_etl.kiwoom_conform). Reads only.

    python scripts/run_kiwoom_conform.py check --run-id N   # one kiwoom_rest_bronze run
    python scripts/run_kiwoom_conform.py check --latest     # each account's latest successful bronze run

Each run's silver rows are rebuilt from its stored answers with the collector's own functions and
compared with silver; Kiwoom is never called. Needs DATABASE_URL (PostgreSQL, where
bronze.kiwoom_responses lives) and optionally PORTFOLIO_TENANT_ID. Everything runs in one read-only
transaction that is rolled back. Prints one JSON summary of counts, never figures, account numbers,
request keys or bodies. Exits 0 whatever the differences; 2 on a usage error or a run that is not a
Kiwoom bronze run; 1 when the database (or anything else) fails, naming only the error's type.
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

from ibkr_etl.db import DEFAULT_TENANT_ID, connect  # noqa: E402
from ibkr_etl.kiwoom_conform import NotABronzeRun, check_run, latest_runs  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild Kiwoom silver rows from bronze and compare (read-only).")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="compare silver with what the stored answers rebuild")
    which = check.add_mutually_exclusive_group(required=True)
    which.add_argument("--run-id", type=int, help="one kiwoom_rest_bronze ingestion run")
    which.add_argument("--latest", action="store_true", help="each account's latest successful bronze run")
    return parser


def _error(message: str, code: int) -> int:
    print(json.dumps({"status": "error", "error": message}))
    return code


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)  # a usage error exits 2 here
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        return _error("DATABASE_URL must name the PostgreSQL database", 2)
    try:
        connection = connect(Path("/tmp/unused.sqlite3"), database_url,
                             os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID))
    except Exception as error:  # noqa: BLE001 - its text could name the server
        return _error(type(error).__name__, 1)
    try:
        # First in the transaction, so the database refuses any write; the transaction is rolled back below.
        connection.execute("SET TRANSACTION READ ONLY")
        run_ids = latest_runs(connection) if args.latest else [args.run_id]
        results = [check_run(connection, run_id) for run_id in run_ids]
    except NotABronzeRun as error:  # our own message: the run id only
        return _error(str(error), 2)
    except Exception as error:  # noqa: BLE001 - only the type: a database message could quote a row
        return _error(type(error).__name__, 1)
    finally:
        for step in (connection.rollback, connection.close):
            try:
                step()
            except Exception:  # noqa: BLE001 - nothing was written, and the summary is already decided
                pass
    print(json.dumps({"status": "checked", "mode": "read-only", "runs": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
