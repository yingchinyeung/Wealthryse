"""Load daily factor returns into silver (docs/PHASE3_DATA.md, job ibkrv2stg-factor-returns).

  python scripts/load_factor_returns.py [--region us|developed|asia_pacific_ex_japan ...] [--etf-proxy]

Does nothing, and exits 0, unless FACTOR_RETURNS_LOAD=on. Then, for each region (default all three
fama_french.py uses), it downloads Kenneth French's daily five-factor and momentum files, stores
each response as received in bronze-market-data (feed kenneth-french, when MARKET_DATA_RAW_CONTAINER
is set), parses them and upserts silver.factor_returns with the file's vintage, one transaction per
region. --etf-proxy also stores the ETF stand-ins (source etf-proxy) for the days after the
library's latest; without it they are never stored.

It prints one JSON line per region, then a summary. Exit 0; 1 when a file or region failed; 65 when
the held migration has not run yet (the table is missing), having fetched and written nothing.
Runs as its own job on Saturdays at 06:00 UTC, outside the daily 07:50 to 09:25 UTC window.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import factor_returns as fr  # noqa: E402
from ibkr_etl import fama_french, lineage, raw_capture  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402

JOB = "ibkrv2stg-factor-returns"


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load Kenneth French daily factor returns into silver.")
    parser.add_argument("--region", action="append", default=[], choices=sorted(fama_french.REGIONS),
                        help="a region (repeatable; default all)")
    parser.add_argument("--etf-proxy", action="store_true",
                        help="also store the ETF stand-ins (source etf-proxy) after the library's latest day")
    return parser.parse_args(argv)


def _off() -> int:
    print(json.dumps({"status": "off", "setting": fr.SETTING,
                      "note": f"set {fr.SETTING}=on to load the factor returns"}, sort_keys=True), flush=True)
    return 0


def _held() -> int:
    print(json.dumps({"status": "held", "table": fr.TABLE, "exit_code": fr.EXIT_HELD,
                      "note": "the held migration that creates the table has not run; nothing was fetched"},
                     sort_keys=True), flush=True)
    return fr.EXIT_HELD


def main(argv: list[str] | None = None, *, fetch=None, proxies=None) -> int:
    if not fr.load_enabled():
        return _off()
    args = _arguments(argv)
    settings = load_settings()
    if not settings.database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("The factor returns load requires a PostgreSQL DATABASE_URL")
    connection = connect(settings.database_path, settings.database_url, settings.tenant_id)
    try:
        if not fr.table_exists(connection):
            return _held()
        # The raw copies name this run (blob metadata job_run_id), as lineage names them.
        raw_capture.set_job_run_id(lineage.current().id)
        try:
            result = fr.load(connection, args.region or None, fetch=fetch or fr.download,
                             etf_proxy=args.etf_proxy, proxies=proxies, record=lineage.current())
        except Exception as error:
            if getattr(error, "sqlstate", None) == "42P01":
                connection.rollback()
                return _held()
            raise
        for item in result["regions"]:
            print(json.dumps(item, sort_keys=True), flush=True)
        status = "failed" if result["failed"] else "success"
        print(json.dumps({"status": status, "written": result["written"], "failed": result["failed"],
                          "etf_proxy": result["etf_proxy"], "raw_capture": raw_capture.default().summary()},
                         sort_keys=True), flush=True)
        return 1 if result["failed"] else 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def entry(argv: list[str] | None = None) -> int:
    """Off: nothing at all, not even a lineage record. On: the run, recorded under its job name."""
    if not fr.load_enabled():
        return _off()
    return lineage.run_main(JOB, lambda: main(argv))


if __name__ == "__main__":
    raise SystemExit(entry())
