"""Load SEC EDGAR 13F-HR holdings into silver institutional_holdings (docs/PHASE3_DATA_MACRO_13F.md, job
ibkrv2stg-institutional-holdings, 22:45 UTC on the 20th of Feb, May, Aug and Nov).

  python scripts/load_institutional_holdings.py [--quarters N] [--refresh] [--cik CIK ...] [--minutes N]

Does nothing, and exits 0, unless INSTITUTIONAL_HOLDINGS_LOAD=on. Then it refuses to start (exit 78)
unless SEC_USER_AGENT names the operator with a contact email address, as SEC fair access asks, and
the filer list (src/ibkr_etl/data/sec_13f_filers.json) is marked reviewed. It reads each filer's
13F-HR and 13F-HR/A filings for the last --quarters quarter ends (default 4), at most 4 requests a
second, each response captured to bronze-market-data sec-13f/ before it is parsed, and stores each
filing in its own transaction. Accessions already stored are skipped unless --refresh, so the first
run is the backfill of the last 4 quarters and each quarterly run after it adds the new quarter and
any late amendment. --cik limits the run to
filers on the list. No new filer or filing starts once --minutes (default 45) are spent or the
daily chain's window (07:50 to 09:25 UTC) begins; the next run picks up what was left.

It prints one JSON line per filing read (stored, skipped or failed), then a summary. Exit codes: 0
done (or off); 1 a filer or filing failed; 65 the table's held migration has not run; 75 started
inside the daily window; 78 no usable SEC_USER_AGENT, or the filer list is not reviewed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import lineage, raw_capture  # noqa: E402
from ibkr_etl import sec_13f  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402

JOB = "ibkrv2stg-institutional-holdings"


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load SEC 13F-HR holdings into silver institutional_holdings.")
    parser.add_argument("--quarters", type=int, default=sec_13f.DEFAULT_QUARTERS,
                        help=f"quarter ends to read, newest first (default {sec_13f.DEFAULT_QUARTERS})")
    parser.add_argument("--refresh", action="store_true", help="read again filings already stored")
    parser.add_argument("--cik", action="append", default=[], help="a filer CIK from the list (repeatable)")
    parser.add_argument("--minutes", type=float, default=sec_13f.DEFAULT_MINUTES,
                        help=f"time budget (default {sec_13f.DEFAULT_MINUTES:g})")
    args = parser.parse_args(argv)
    if not 1 <= args.quarters <= 8:
        parser.error("--quarters must be from 1 to 8")
    if not 0 < args.minutes <= 600:
        parser.error("--minutes must be above 0 and at most 600")
    return args


def _print(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def _off() -> int:
    _print({"status": "off", "setting": sec_13f.SETTING,
            "note": f"set {sec_13f.SETTING}=on to load the 13F holdings"})
    return 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _filers(ciks: list[str]) -> list[sec_13f.Filer]:
    filers = sec_13f.load_filers()
    if not ciks:
        return filers
    wanted = {str(cik).strip().zfill(10) for cik in ciks}
    unknown = sorted(wanted - {filer.cik for filer in filers})
    if unknown:
        raise SystemExit(f"--cik {', '.join(unknown)} is not on the reviewed filer list")
    return [filer for filer in filers if filer.cik in wanted]


def main(argv: list[str] | None = None) -> int:
    if not sec_13f.enabled():
        return _off()
    args = _arguments(argv)
    # Refusals come before any database or network work.
    try:
        agent = sec_13f.user_agent()
        filers = _filers(args.cik)
    except (sec_13f.UserAgentMissing, sec_13f.FilerListNotReviewed) as error:
        _print({"status": "refused", "reason": str(error)})
        return sec_13f.EXIT_REFUSED
    if sec_13f.in_daily_window(_now()):
        _print({"status": "daily_window", "note": "started inside 07:50-09:25 UTC; nothing done, run it after"})
        return sec_13f.EXIT_DAILY_WINDOW
    started = time.monotonic()
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("The institutional holdings job requires DATABASE_URL")
    connection = connect(settings.database_path, settings.database_url, settings.tenant_id)
    try:
        if not sec_13f.relation_present(connection):
            _print({"status": "held", "relation": sec_13f.RELATION,
                    "note": "the held migration infra/medallion/drafts/institutional_holdings.sql has not run; "
                            "nothing done"})
            return sec_13f.EXIT_HELD
        run_id = raw_capture.new_run_id("institutional-holdings")
        raw_capture.set_job_run_id(run_id)
        lineage.current().facet(job_run_id=run_id)
        since = sec_13f.quarter_ends(_now().date(), args.quarters)[-1]
        client = sec_13f.SecClient(agent)

        def stop() -> bool:
            return time.monotonic() - started >= args.minutes * 60 or sec_13f.in_daily_window(_now())

        result = sec_13f.load(connection, client, filers, since=since, refresh=args.refresh, stop=stop,
                              emit=_print)
        lineage.current().output(sec_13f.TABLE, rows=int(result["rows_stored"]))
        status = "failed" if result["failed"] else "stopped" if result["not_started"] else "success"
        _print({"status": status, "job_run_id": run_id, "quarters": args.quarters, "minutes": args.minutes,
                "elapsed_seconds": round(time.monotonic() - started, 1), **result})
        return 1 if result["failed"] else 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def entry(argv: list[str] | None = None) -> int:
    """Off: nothing at all, not even a lineage record. On: the run, recorded under its job name."""
    if not sec_13f.enabled():
        return _off()
    return lineage.run_main(JOB, lambda: main(argv))


if __name__ == "__main__":
    raise SystemExit(entry())
