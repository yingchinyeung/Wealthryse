"""Load the daily macro series into silver macro_series (docs/PHASE3_DATA_MACRO_13F.md, job
ibkrv2stg-macro-series, 22:30 UTC).

  python scripts/load_macro_series.py [--since YYYY-MM-DD]

Does nothing, and exits 0, unless MACRO_SERIES_LOAD=on. Then, for each series whose licence lets us
keep it (ibkr_etl.macro_series.SERIES: the FRED Treasury yields, broad dollar and Brent, and Yahoo
USD/CNH; VIXCLS only once MACRO_SERIES_LICENSE_CONFIRMED names it), it downloads the history from
--since (default 2015-01-01), captures the raw response to bronze-market-data, parses it ('.' days
skipped) and stores what is new or revised under today's vintage, one transaction per series.

Exit codes: 0 done (or off); 1 a series failed (the others are stored); 65 the table's held
migration has not run, so nothing was fetched or written; 75 started inside the daily chain's
window (07:50 to 09:25 UTC), so nothing was done. It prints one JSON summary line.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import lineage, raw_capture  # noqa: E402
from ibkr_etl import macro_series as ms  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402

JOB = "ibkrv2stg-macro-series"


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load the daily macro series into silver macro_series.")
    parser.add_argument("--since", type=date.fromisoformat, default=ms.DEFAULT_START,
                        help=f"first observation date to fetch (default {ms.DEFAULT_START.isoformat()})")
    return parser.parse_args(argv)


def _print(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def _off() -> int:
    _print({"status": "off", "setting": ms.SETTING, "note": f"set {ms.SETTING}=on to load the macro series"})
    return 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def main(argv: list[str] | None = None) -> int:
    if not ms.enabled():
        return _off()
    args = _arguments(argv)
    if ms.in_daily_window(_now()):
        _print({"status": "daily_window", "note": "started inside 07:50-09:25 UTC; nothing done, run it after"})
        return ms.EXIT_DAILY_WINDOW
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("The macro series job requires DATABASE_URL")
    connection = connect(settings.database_path, settings.database_url, settings.tenant_id)
    try:
        if not ms.relation_present(connection):
            _print({"status": "held", "relation": ms.RELATION,
                    "note": "the held migration infra/medallion/drafts/macro_series.sql has not run; nothing done"})
            return ms.EXIT_HELD
        run_id = raw_capture.new_run_id("macro-series")
        raw_capture.set_job_run_id(run_id)
        lineage.current().facet(job_run_id=run_id)
        result = ms.load(connection, start=args.since, stop=lambda: ms.in_daily_window(_now()))
        lineage.current().output(ms.TABLE, rows=int(result["rows_stored"]))
        status = "failed" if result["failed"] else "stopped" if result["not_started"] else "success"
        _print({"status": status, "job_run_id": run_id, **result})
        return 1 if result["failed"] else 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def entry(argv: list[str] | None = None) -> int:
    """Off: nothing at all, not even a lineage record. On: the run, recorded under its job name."""
    if not ms.enabled():
        return _off()
    return lineage.run_main(JOB, lambda: main(argv))


if __name__ == "__main__":
    raise SystemExit(entry())
