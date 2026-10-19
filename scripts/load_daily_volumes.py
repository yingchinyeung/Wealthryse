"""Load daily traded volumes into silver (docs/PHASE3_DATA.md, job ibkrv2stg-daily-volumes).

  python scripts/load_daily_volumes.py [--days N] [--instrument ID ...] [--fetch [--max-symbols N]]
  python scripts/load_daily_volumes.py --from YYYY-MM-DD [--to YYYY-MM-DD] [--instrument ID ...]

Does nothing, and exits 0, unless DAILY_VOLUMES_LOAD=on. Then it reads the Yahoo chart responses
already kept in bronze-market-data (feed yahoo-chart) for the chart instruments of
market_data_prices and upserts silver.daily_volumes, one transaction per instrument. The daily run
reads the last N dated capture folders (default 3). --from is a backfill: every capture kept,
history/ included, for the days from that date (to --to, default today).

--fetch (the job passes it) first fetches the holdings' own charts, but only when
DAILY_VOLUMES_FETCH=on; otherwise it fetches nothing and says so. For every instrument held by a
physical account on its latest position date (silver), it works out the Yahoo symbol as the
platform does and asks Yahoo's chart API through market_data, which keeps each response raw in
bronze-market-data first (MARKET_DATA_RAW_CONTAINER must name it). The volumes are then parsed from
those captures and stored under the holding's conid. A pause between requests, at most
--max-symbols requests (default 60) and a time budget per run; the rest wait for the next run.
Holdings it cannot map are listed, never guessed. --instrument then names conids too.

It prints one JSON line per instrument (the holdings first), then a summary. Exit 0; 1 when an
instrument could not be written, a capture could not be read, or a holding's fetch failed or was
not kept (or raw capture is not set up); 65 when the held migration has not run yet (the table is
missing), having read, fetched and written nothing. Runs as its own job at 06:30 UTC, outside the
daily 07:50 to 09:25 UTC window, and never inside the jobs the publish gate binds.
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
from ibkr_etl import lineage, raw_capture  # noqa: E402
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
    parser.add_argument("--fetch", action="store_true",
                        help=f"first fetch the holdings' own charts (only when {dv.FETCH_SETTING}=on)")
    parser.add_argument("--max-symbols", type=int, default=dv.DEFAULT_MAX_SYMBOLS,
                        help=f"with --fetch: Yahoo requests per run at most (default {dv.DEFAULT_MAX_SYMBOLS})")
    args = parser.parse_args(argv)
    if args.until is not None and args.since is None:
        parser.error("--to needs --from")
    if args.since is not None and args.until is not None and args.until < args.since:
        parser.error("--to is before --from")
    if args.fetch and args.since is not None:
        parser.error("--fetch reads the daily capture folders, so it does not go with --from")
    if args.max_symbols < 1:
        parser.error("--max-symbols must be 1 or more")
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


def _fetch(connection, source, args, options) -> dict:
    """The holdings' fetch and load, or a note that the setting is off (nothing read or fetched)."""
    if not dv.fetch_enabled():
        return {"setting": "off", "status": "off",
                "note": f"set {dv.FETCH_SETTING}=on to fetch the holdings' charts; nothing was fetched"}
    # The raw copies name this run (blob metadata job_run_id), as lineage names them.
    raw_capture.set_job_run_id(lineage.current().id)
    for name in dv.FETCH_INPUTS:
        lineage.current().input(name)
    return dv.fetch_and_load(connection, source, days=args.days, only=args.instrument,
                             max_symbols=args.max_symbols, record=lineage.current(), **options)


def main(argv: list[str] | None = None, *, bronze: dv.BronzeReader | None = None, fetcher=None, sleep=None,
         clock=None) -> int:
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
        options = {key: value for key, value in (("fetcher", fetcher), ("sleep", sleep), ("clock", clock))
                   if value is not None}
        try:
            fetched = _fetch(connection, source, args, options) if args.fetch else None
            result = dv.load(connection, source, days=args.days, since=args.since, until=args.until,
                             only=args.instrument, record=lineage.current())
        except Exception as error:
            if getattr(error, "sqlstate", None) == "42P01":
                connection.rollback()
                return _held()
            raise
        for item in (fetched or {}).get("instruments", []) + result["instruments"]:
            print(json.dumps(item, sort_keys=True), flush=True)
        unreadable = sum(len(item["unreadable_captures"]) for item in result["instruments"])
        fetch_failed = fetched is not None and fetched["status"] in ("failed", "capture_off")
        kept = fetched is not None and fetched.get("captured", 0) > 0
        # No capture at all is not a failure (the chart fetch may run with raw capture off), but says so.
        status = ("failed" if result["failed"] or unreadable or fetch_failed else
                  "no_captures" if not kept and result["instruments"] and len(result["without_captures"]) == len(
                      result["instruments"]) else "success")
        summary = {"status": status, **{k: v for k, v in result.items() if k != "instruments"},
                   "unreadable_captures": unreadable}
        if fetched is not None:
            summary["fetch"] = {k: v for k, v in fetched.items() if k != "instruments"}
        print(json.dumps(summary, sort_keys=True), flush=True)
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
