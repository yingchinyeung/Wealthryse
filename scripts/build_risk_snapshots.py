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

from ibkr_etl import lineage  # noqa: E402
from ibkr_etl.config import load_settings  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl import input_readiness  # noqa: E402
from ibkr_etl.risk_snapshots import (  # noqa: E402
    build_risk_snapshot,
    RiskInputsNotReady,
)
from ibkr_etl.subject_read_contract import ACTIVE_NAV_CURRENCY_SQL  # noqa: E402
from ibkr_etl.risk_refresh import (audit_risk_history, mark_cache_check, risk_date_lock,
                                  inception_without_comparisons)  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build versioned deterministic risk snapshots outside web requests."
    )
    parser.add_argument("--subject", action="append", default=[])
    parser.add_argument("--history-audit-limit", type=int, default=20,
                        help="Maximum existing historical caches to source-check per invocation (1–100).")
    parser.add_argument("--history-rebuild-limit", type=int, default=2,
                        help="Maximum changed historical caches to rebuild per invocation.")
    parser.add_argument("--no-history-audit", action="store_true")
    parser.add_argument(
        "--all-dates",
        action="store_true",
        default=os.environ.get("RISK_SNAPSHOT_ALL_DATES", "").strip().lower()
        in {"1", "true", "yes", "on"},
        help="Recheck every NAV date and rebuild changed inputs. Daily default checks the latest date.",
    )
    args=parser.parse_args()
    if not 1 <= args.history_audit_limit <= 100:
        parser.error("History audit limit must be between one and one hundred")
    if not 0 <= args.history_rebuild_limit <= args.history_audit_limit:
        parser.error("Rebuild limit must fit inside the bounded audit")
    return args


def main() -> int:
    input_readiness.gate("risk-snapshots")
    args = _arguments()
    settings = load_settings()
    if not settings.database_url:
        raise RuntimeError("Risk snapshot generation requires DATABASE_URL")
    connection = connect(
        settings.database_path, settings.database_url, settings.tenant_id
    )
    # With lineage recording on, the gold rows this session writes name this run.
    lineage.current().bind(connection)
    results: list[dict] = []
    skipped = 0
    blocked = []
    unavailable = []
    try:
        subjects = args.subject or [
            str(row["subject_id"])
            for row in connection.execute(
                """
                SELECT DISTINCT m.subject_id
                FROM portfolio_benchmark_mapping m
                JOIN platform_subjects s ON s.tenant_id=m.tenant_id AND s.subject_id=m.subject_id
                WHERE m.tenant_id = ?
                  AND s.lifecycle_status='active'
                  AND EXISTS (
                    SELECT 1 FROM subject_operating_registrations r
                    WHERE r.tenant_id=m.tenant_id AND r.subject_id=m.subject_id
                      AND r.capability='benchmark_refresh' AND r.operating_state='active'
                      AND r.effective_from <= (now() AT TIME ZONE 'Asia/Hong_Kong')::date
                      AND (r.effective_to IS NULL OR r.effective_to >= (now() AT TIME ZONE 'Asia/Hong_Kong')::date)
                  )
                  AND EXISTS (
                    SELECT 1 FROM subject_nav_series n
                    WHERE n.tenant_id = m.tenant_id
                      AND n.subject_id = m.subject_id
                  )
                ORDER BY m.subject_id
                """,
                (settings.tenant_id,),
            ).fetchall()
        ]
        for subject_id in subjects:
            if args.all_dates:
                dates = [
                    str(row["snapshot_date"])
                    for row in connection.execute(
                        """
                        SELECT DISTINCT snapshot_date
                        FROM subject_nav_series
                        WHERE tenant_id = ? AND subject_id = ? AND currency =
                        """ + ACTIVE_NAV_CURRENCY_SQL + ' ORDER BY snapshot_date',
                        (settings.tenant_id, subject_id),
                    ).fetchall()
                ]
            else:
                latest = connection.execute(
                    """
                    SELECT MAX(snapshot_date) AS latest
                    FROM subject_nav_series
                    WHERE tenant_id = ? AND subject_id = ? AND currency =
                    """ + ACTIVE_NAV_CURRENCY_SQL,
                    (settings.tenant_id, subject_id),
                ).fetchone()["latest"]
                dates = [str(latest)] if latest else []
            for as_of in dates:
                # Use one consistent input snapshot per date. No web-side bootstrap.
                scope=dict(tenant_id=settings.tenant_id,subject_id=subject_id,as_of=as_of)
                connection.commit()
                with risk_date_lock(connection,**scope) as acquired:
                    if not acquired:
                        blocked.append(dict(subject_id=subject_id,as_of=as_of,reason="Another risk worker owns this date"))
                        continue
                    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    try:
                        result=build_risk_snapshot(connection,**scope,only_if_changed=True)
                    except RiskInputsNotReady as error:
                        connection.rollback()
                        if inception_without_comparisons(connection,**scope):
                            mark_cache_check(connection,**scope,status='insufficient_history',
                                reason='No comparison-return observations exist at portfolio inception',invalidate=True)
                            unavailable.append(dict(subject_id=subject_id,as_of=as_of,reason='Insufficient inception return history'))
                            continue
                        mark_cache_check(connection,**scope,status="blocked",reason=str(error),invalidate=True)
                        blocked.append(dict(subject_id=subject_id,as_of=as_of,reason=str(error)))
                        continue
                    mark_cache_check(connection,**scope,status="current")
                if result["status"] == "unchanged":
                    skipped += 1
                    continue
                results.append(result)
                print(json.dumps(result, sort_keys=True), flush=True)
        # Explicit onboarding subjects remain scoped to their requested work.
        # Default scheduled operation audits only the active registry, never
        # reactivates paused portfolios and never changes published reports.
        historical=None
        if not args.no_history_audit and not args.subject and not args.all_dates:
            historical=audit_risk_history(connection,tenant_id=settings.tenant_id,subjects=subjects,
                audit_limit=args.history_audit_limit,rebuild_limit=args.history_rebuild_limit)
            print(json.dumps({"historical_audit":historical},sort_keys=True),flush=True)
        lineage.current().output("gold.risk_analytics_snapshots",
                                 rows=len(results)+int((historical or {}).get("rebuilt") or 0))
        print(
            json.dumps(
                {
                    "status": "blocked" if blocked or historical and (historical["blocked"] or historical["pending"] or historical["busy"]) else "success",
                    "subjects": len(set(row["subject_id"] for row in results)),
                    "snapshots": len(results),
                    "skipped_existing": skipped,
                    "mode": "all-dates" if args.all_dates else "latest",
                    "blocked_subject_dates":blocked,
                    "unavailable_inception_dates":unavailable,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 1 if blocked or historical and (historical["blocked"] or historical["pending"] or historical["busy"]) else 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(lineage.run_main("ibkrv2stg-risk-snapshots", main))
