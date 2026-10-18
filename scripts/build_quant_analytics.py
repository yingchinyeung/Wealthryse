"""Build the gold quant analytics sections (docs/QUANT_ANALYTICS.md, job ibkrv2stg-quant-analytics).

  python scripts/build_quant_analytics.py [--subject ID ...] [--since YYYY-MM-DD] [--minutes N]

Does nothing, and exits 0, unless QUANT_ANALYTICS_BUILD=on. Then, for each subject the risk
snapshot job builds (active, benchmark refresh registered, with NAV), it builds every section for
the subject's latest NAV date; --since adds every NAV date from that day on (a bounded backfill)
and --subject names the subjects instead. The newest dates go first. Each subject and date is
built under its own advisory lock and written in one transaction, the publish gate called before
the commit, so a reader never sees a half-written day.

The run starts no new subject-date once QUANT_ANALYTICS_MINUTES (default 8) are spent, so the
09:30 run ends by 09:40, before the second certified run at 09:45; what it skipped is in the
summary and the next run picks it up. After writing it runs the table's own contract rules on the
dates it built and reports them. It prints one JSON line per subject-date it rebuilt, then a
summary. Exit 0, 1 when a subject-date failed or a rule of severity error found bad rows, or 65
when the publish gate (enforce) held a version back.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl import lineage, publish_gate  # noqa: E402
from ibkr_etl import quant_snapshots as qs  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.subject_read_contract import ACTIVE_NAV_CURRENCY_SQL  # noqa: E402

# The subjects the risk snapshot job builds (scripts/build_risk_snapshots.py).
SUBJECTS_SQL = """
SELECT DISTINCT m.subject_id
FROM portfolio_benchmark_mapping m
JOIN platform_subjects s ON s.tenant_id = m.tenant_id AND s.subject_id = m.subject_id
WHERE m.tenant_id = ?
  AND s.lifecycle_status = 'active'
  AND EXISTS (
    SELECT 1 FROM subject_operating_registrations r
    WHERE r.tenant_id = m.tenant_id AND r.subject_id = m.subject_id
      AND r.capability = 'benchmark_refresh' AND r.operating_state = 'active'
      AND r.effective_from <= (now() AT TIME ZONE 'Asia/Hong_Kong')::date
      AND (r.effective_to IS NULL OR r.effective_to >= (now() AT TIME ZONE 'Asia/Hong_Kong')::date)
  )
  AND EXISTS (
    SELECT 1 FROM subject_nav_series n
    WHERE n.tenant_id = m.tenant_id AND n.subject_id = m.subject_id
  )
ORDER BY m.subject_id
"""
NAV_DATES_SQL = ("SELECT DISTINCT snapshot_date FROM subject_nav_series "
                 "WHERE tenant_id = ? AND subject_id = ? AND snapshot_date >= ? AND currency = "
                 + ACTIVE_NAV_CURRENCY_SQL + " ORDER BY snapshot_date DESC")
LATEST_NAV_SQL = ("SELECT MAX(snapshot_date) AS latest FROM subject_nav_series "
                  "WHERE tenant_id = ? AND subject_id = ? AND currency = " + ACTIVE_NAV_CURRENCY_SQL)


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the gold quant analytics sections.")
    parser.add_argument("--subject", action="append", default=[], help="a subject id (repeatable)")
    parser.add_argument("--since", type=date.fromisoformat, default=None,
                        help="also build every NAV date from this day (YYYY-MM-DD)")
    parser.add_argument("--minutes", type=float, default=None,
                        help=f"time budget (default {qs.MINUTES_SETTING}, else {qs.DEFAULT_MINUTES:g})")
    return parser.parse_args(argv)


def _work(connection, tenant_id: str, subjects: list[str], since: date | None) -> list[tuple[str, str]]:
    """(subject, date) pairs, newest dates first, so a spent budget leaves the oldest."""
    pairs = []
    for subject_id in subjects:
        if since is None:
            latest = connection.execute(LATEST_NAV_SQL, (tenant_id, subject_id)).fetchone()["latest"]
            dates = [str(latest)[:10]] if latest else []
        else:
            dates = [str(row["snapshot_date"])[:10] for row in
                     connection.execute(NAV_DATES_SQL, (tenant_id, subject_id, since.isoformat())).fetchall()]
        pairs += [(subject_id, day) for day in dates]
    connection.rollback()
    return sorted(pairs, key=lambda pair: (pair[1], pair[0]), reverse=True)


def _off() -> int:
    print(json.dumps({"status": "off", "setting": qs.BUILD_SETTING,
                      "note": f"set {qs.BUILD_SETTING}=on to build the quant analytics"}, sort_keys=True), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    if not qs.build_enabled():
        return _off()
    args = _arguments(argv)
    minutes = qs.time_limit(args.minutes)
    started = time.monotonic()
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("The quant analytics build requires DATABASE_URL")
    connection = connect(settings.database_path, settings.database_url, settings.tenant_id)
    # With lineage recording on, the gold rows this session writes name this run.
    lineage.current().bind(connection)
    tenant_id = settings.tenant_id
    rebuilt, unchanged, skipped, blocked, held_back, failed = [], [], [], [], [], []
    seconds: dict[str, float] = {}
    sections_written = 0
    try:
        subjects = args.subject or [str(row["subject_id"]) for row in
                                    connection.execute(SUBJECTS_SQL, (tenant_id,)).fetchall()]
        work = _work(connection, tenant_id, subjects, args.since)
        for subject_id, as_of in work:
            if time.monotonic() - started >= minutes * 60:
                skipped.append({"subject_id": subject_id, "as_of": as_of, "reason": "time budget spent"})
                continue
            scope = {"tenant_id": tenant_id, "subject_id": subject_id, "as_of": as_of}
            pair_started = time.monotonic()
            with qs.quant_date_lock(connection, **scope) as acquired:
                if not acquired:
                    blocked.append({"subject_id": subject_id, "as_of": as_of,
                                    "reason": "another quant worker owns this date"})
                    continue
                # One consistent read of the inputs for the whole subject-date.
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                try:
                    result = qs.build_quant_snapshot(connection, **scope)
                except publish_gate.BuildHeldBack as error:
                    held_back.append({"subject_id": subject_id, "as_of": as_of, "reason": str(error)[:500]})
                    continue
                except Exception as error:  # noqa: BLE001 - one subject-date never stops the others
                    connection.rollback()
                    failed.append({"subject_id": subject_id, "as_of": as_of,
                                   "error": f"{type(error).__name__}: {error}"[:500]})
                    continue
            seconds[f"{subject_id}@{as_of}"] = round(time.monotonic() - pair_started, 3)
            sections_written += result["written"]
            if result["errors"]:
                failed.append({"subject_id": subject_id, "as_of": as_of, "sections": result["errors"]})
            if result["status"] == "unchanged":
                unchanged.append({"subject_id": subject_id, "as_of": as_of})
                continue
            rebuilt.append({"subject_id": subject_id, "as_of": as_of})
            print(json.dumps(result, sort_keys=True), flush=True)
        built_dates = sorted({item["as_of"] for item in rebuilt + unchanged})
        rules = qs.check_contract_rules(connection, as_of_dates=built_dates) if built_dates else []
        broken = [rule for rule in rules if not rule.get("passed") and rule.get("severity") == "error"]
        lineage.current().output(qs.TABLE, rows=sections_written)
        status = ("held_back" if held_back else "failed" if failed or broken else "blocked" if blocked
                  else "budget_spent" if skipped else "success")
        print(json.dumps({
            "status": status, "subjects": len(subjects), "subject_dates": len(work), "rebuilt": len(rebuilt),
            "unchanged": len(unchanged), "sections_written": sections_written, "minutes": minutes,
            "elapsed_seconds": round(time.monotonic() - started, 1), "seconds_per_subject_date": seconds,
            "skipped_for_time": skipped, "blocked": blocked, "held_back": held_back, "failed": failed,
            "quality_rules": rules,
        }, sort_keys=True), flush=True)
        if held_back:
            return publish_gate.EXIT_REJECTED
        return 1 if failed or broken or blocked else 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def entry(argv: list[str] | None = None) -> int:
    """Off: nothing at all, not even a lineage record. On: the run, recorded under its job name."""
    if not qs.build_enabled():
        return _off()
    return lineage.run_main("ibkrv2stg-quant-analytics", lambda: main(argv))


if __name__ == "__main__":
    raise SystemExit(entry())
