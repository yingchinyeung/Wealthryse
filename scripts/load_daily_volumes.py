"""Load daily traded volumes into silver (docs/PHASE3_DATA.md, job ibkrv2stg-daily-volumes).

  python scripts/load_daily_volumes.py [--days N] [--instrument ID ...]
  python scripts/load_daily_volumes.py --from YYYY-MM-DD [--to YYYY-MM-DD] [--instrument ID ...]

Does nothing, and exits 0, unless DAILY_VOLUMES_LOAD=on. Then it reads the Yahoo chart responses
already kept in bronze-market-data (feed yahoo-chart; no new fetch) for the chart instruments of
market_data_prices and upserts silver.daily_volumes, one transaction per instrument. The daily run
reads the last N dated capture folders (default 3). --from is a backfill: every capture kept,
history/ included, for the days from that date (to --to, default today).

It prints one JSON line per instrument, then a summary. Exit 0; 1 when an instrument could not be
written or a capture could not be read; 65 when the held migration has not run yet (the table is
missing), having read and written nothing. Runs as its own job at 06:30 UTC, outside the daily
07:50 to 09:25 UTC window, and never inside the jobs the publish gate binds.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import daily_volumes as dv  # noqa: E402
from ibkr_etl import lineage  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402

JOB = "ibkrv2stg-daily-volumes"


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load daily volumes from the kept Yahoo chart captures.")
    parser.add_argument("--days", type=int, default=dv.DEFAULT_DAYS,
                        help=f"dated capture folders to read, newest first (default {dv.DEFAULT_DAYS})")
    parser.add_argument("--from", dest="since", type=date.fromisoformat, default=None,
                        help="backfill the days from this date (YYYY-MM-DD) from every capture kept")
    parser.add_argument("--to", dest="until", type=date.fromisoformat, default=None,
                        help="with --from: the last day to backfill (default today)")
    parser.add_argument("--instrument", action="append", default=[], help="an instrument_id (repeatable)")
    args = parser.parse_args(argv)
    if args.until is not None and args.since is None:
        parser.error("--to needs --from")
    if args.since is not None and args.until is not None and args.until < args.since:
        parser.error("--to is before --from")
    return args


def _off() -> int:
    print(json.dumps({"status": "off", "setting": dv.SETTING,
                      "note": f"set {dv.SETTING}=on to load the daily volumes"}, sort_keys=True), flush=True)
    return 0


def _held() -> int:
    print(json.dumps({"status": "held", "table": dv.TABLE, "exit_code": dv.EXIT_HELD,
                      "note": "the held migration that creates the table has not run; nothing was read"},
                     sort_keys=True), flush=True)
    return dv.EXIT_HELD


def main(argv: list[str] | None = None, *, bronze: dv.BronzeReader | None = None) -> int:
    if not dv.load_enabled():
        return _off()
    args = _arguments(argv)
    settings = load_settings()
    if not settings.database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("The daily volumes load requires a PostgreSQL DATABASE_URL")
    connection = connect(settings.database_path, settings.database_url, settings.tenant_id)
    try:
        if not dv.table_exists(connection):
            return _held()
        lineage.current().input("silver.market_data_prices")
        source = bronze if bronze is not None else dv.AzureBronze.from_environment()
        try:
            result = dv.load(connection, source, days=args.days, since=args.since, until=args.until,
                             only=args.instrument, record=lineage.current())
        except Exception as error:
            if getattr(error, "sqlstate", None) == "42P01":
                connection.rollback()
                return _held()
            raise
        for item in result["instruments"]:
            print(json.dumps(item, sort_keys=True), flush=True)
        unreadable = sum(len(item["unreadable_captures"]) for item in result["instruments"])
        # No capture at all is not a failure (the chart fetch may run with raw capture off), but says so.
        status = ("failed" if result["failed"] or unreadable else
                  "no_captures" if result["instruments"] and len(result["without_captures"]) == len(
                      result["instruments"]) else "success")
        print(json.dumps({"status": status, **{k: v for k, v in result.items() if k != "instruments"},
                          "unreadable_captures": unreadable}, sort_keys=True), flush=True)
        return 1 if status == "failed" else 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def entry(argv: list[str] | None = None) -> int:
    """Off: nothing at all, not even a lineage record. On: the run, recorded under its job name."""
    if not dv.load_enabled():
        return _off()
    return lineage.run_main(JOB, lambda: main(argv))


if __name__ == "__main__":
    raise SystemExit(entry())
