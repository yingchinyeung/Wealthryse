from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date

from psycopg.rows import dict_row

from ibkr_etl.burn_in import (
    collect_import_state,
    consecutive_pass_days,
    evaluate_database_state,
    now_hkt_date,
    portal_boundary_check,
)
from ibkr_etl.util import utc_now_iso


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _portal_check(url: str, tenant_id: str) -> tuple[dict, str | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "IBKR-Burn-In-Monitor/1.0"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=90) as response:
            status = int(response.status)
            location = response.headers.get("Location")
    except urllib.error.HTTPError as error:
        status = int(error.code)
        location = error.headers.get("Location")
    except (TimeoutError, urllib.error.URLError, OSError) as error:
        return {
            "status_code": 0,
            "redirect_host": None,
            "redirect_path": None,
            "error_type": type(error).__name__,
        }, "Portal did not respond within the controlled availability-check window"
    return portal_boundary_check(status, location, tenant_id=tenant_id)


def _database_state(database_url: str, tenant_id: str, account_id: str, today_hkt: date) -> dict:
    import psycopg

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        dates = connection.execute(
            """
            SELECT
              (SELECT MAX(snapshot_date) FROM nav_snapshots WHERE account_id = %s) AS latest_nav_date,
              (SELECT MAX(observation_date) FROM benchmark_prices
                WHERE benchmark_id = 'GSPC' AND quality_status <> 'fail') AS latest_gspc_date,
              (SELECT MAX(observation_date) FROM benchmark_prices
                WHERE benchmark_id = 'IXIC' AND quality_status <> 'fail') AS latest_ixic_date,
              (SELECT MAX(rate_date) FROM risk_free_rates
                WHERE currency = 'USD' AND quality_status <> 'fail') AS latest_risk_free_date
            """,
            (account_id,),
        ).fetchone()
        imports = collect_import_state(connection, account_id, as_of=today_hkt)
        latest_snapshot = connection.execute(
            """
            SELECT snapshot_id, period_end, data_as_of, status, quality_status
            FROM reporting_snapshots
            WHERE subject_id = %s AND status IN ('frozen', 'approved')
            ORDER BY period_end DESC, created_at DESC LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        ready_report_types: list[str] = []
        if latest_snapshot:
            ready_report_types = [
                str(row["report_type"])
                for row in connection.execute(
                    """
                    SELECT DISTINCT report_type
                    FROM report_generation_runs
                    WHERE snapshot_id = %s
                      AND report_type IN ('factsheet', 'presentation')
                      AND status IN ('awaiting_approval', 'released_internal', 'published')
                      AND output_reference IS NOT NULL AND output_sha256 IS NOT NULL
                    ORDER BY report_type
                    """,
                    (latest_snapshot["snapshot_id"],),
                ).fetchall()
            ]
        client_approvals = connection.execute(
            """
            SELECT COUNT(*) AS n FROM report_release_decisions
            WHERE release_scope = 'client' AND status = 'approved'
            """
        ).fetchone()
        analyst_assurance = connection.execute(
            """
            SELECT assurance_run_id, assurance_date, as_of_date, completed_at,
                   status, checks_passed, checks_warned, checks_failed,
                   policy_version, model_deployment
            FROM portfolio_analyst_assurance_runs
            WHERE subject_id = %s
            ORDER BY assurance_date DESC, assurance_run_id DESC LIMIT 1
            """,
            (account_id,),
        ).fetchone()
        return {
            **dict(dates),
            **imports,
            "latest_snapshot": None if latest_snapshot is None else dict(latest_snapshot),
            "ready_report_types": ready_report_types,
            "client_approval_count": int(client_approvals["n"]),
            "portfolio_analyst_assurance": (
                None if analyst_assurance is None else dict(analyst_assurance)
            ),
        }


def _record_acceptance_result(
    database_url: str,
    tenant_id: str,
    account_id: str,
    *,
    result_date: date,
    result: dict,
) -> dict:
    import psycopg

    checked_at = utc_now_iso()
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        window = connection.execute(
            """
            SELECT window_id, start_date, earliest_completion_date,
                   required_consecutive_days, status
            FROM burn_in_acceptance_windows
            WHERE subject_id = %s AND status = 'running'
            FOR UPDATE
            """,
            (account_id,),
        ).fetchone()
        if window is None:
            return {"status": "not_started", "consecutive_pass_days": 0}
        daily_status = "pass" if result["status"] == "PASS" else "fail"
        connection.execute(
            """
            INSERT INTO burn_in_daily_results(
                window_id, result_date, status, result_json, checked_at
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, window_id, result_date) DO UPDATE SET
                status = excluded.status, result_json = excluded.result_json,
                checked_at = excluded.checked_at
            """,
            (
                window["window_id"],
                result_date.isoformat(),
                daily_status,
                json.dumps(result, default=str, sort_keys=True, separators=(",", ":")),
                checked_at,
            ),
        )
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT result_date, status FROM burn_in_daily_results
                WHERE window_id = %s ORDER BY result_date DESC
                """,
                (window["window_id"],),
            ).fetchall()
        ]
        count = min(
            consecutive_pass_days(rows, through_date=result_date),
            int(window["required_consecutive_days"]),
        )
        complete = (
            count >= int(window["required_consecutive_days"])
            and result_date >= date.fromisoformat(str(window["earliest_completion_date"]))
        )
        connection.execute(
            """
            UPDATE burn_in_acceptance_windows SET
                consecutive_pass_days = %s,
                last_result_date = %s,
                last_failure_date = CASE WHEN %s = 'fail' THEN %s ELSE last_failure_date END,
                status = CASE WHEN %s THEN 'completed' ELSE 'running' END,
                completed_at = CASE WHEN %s THEN %s ELSE NULL END
            WHERE window_id = %s
            """,
            (
                count,
                result_date.isoformat(),
                daily_status,
                result_date.isoformat(),
                complete,
                complete,
                checked_at,
                window["window_id"],
            ),
        )
        connection.commit()
    return {
        "status": "completed" if complete else "running",
        "window_id": int(window["window_id"]),
        "start_date": str(window["start_date"]),
        "earliest_completion_date": str(window["earliest_completion_date"]),
        "consecutive_pass_days": count,
        "required_consecutive_days": int(window["required_consecutive_days"]),
    }


def main() -> int:
    database_url = _required("DATABASE_URL")
    portal_url = _required("PORTAL_URL")
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    entra_tenant_id = _required("ENTRA_TENANT_ID")
    account_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")
    failures: list[str] = []
    portal, portal_failure = _portal_check(portal_url, entra_tenant_id)
    if portal_failure:
        failures.append(portal_failure)
    today_hkt = now_hkt_date()
    database, database_failures = evaluate_database_state(
        _database_state(database_url, tenant_id, account_id, today_hkt),
        today_hkt=today_hkt,
    )
    failures.extend(database_failures)
    result = {
        "status": "FAIL" if failures else "PASS",
        "account_id": account_id,
        "portal": portal,
        "database": database,
        "failures": failures,
    }
    result["acceptance"] = _record_acceptance_result(
        database_url,
        tenant_id,
        account_id,
        result_date=today_hkt,
        result=result,
    )
    print(json.dumps(result, default=str, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
