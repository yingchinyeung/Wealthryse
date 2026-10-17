from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


HKT = timezone(timedelta(hours=8), name="Asia/Hong_Kong")
FLEX_DAILY_RUN_TYPE = "institutional-flex-daily"


def _date_value(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _hkt_date(value: Any) -> date | None:
    """The HKT date of a stored UTC timestamp (ingestion_runs keeps ISO text)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(HKT).date()


def collect_import_state(connection, account_id: str, *, as_of: date) -> dict[str, Any]:
    """Which feed loads the account's daily data, and that feed's latest import.

    The feed is the account's active daily_ingestion registration on as_of. Until
    it says ibkr_flex, this returns exactly today's v1.1 import evidence. On the
    Flex path the latest import is the latest finished statement request of the
    account's own bound source, so another source's pull never counts.
    connection: psycopg with a dict row factory and the tenant set.
    """
    registration = connection.execute(
        """
        SELECT configuration_json ->> 'source' AS source, effective_from
        FROM subject_operating_registrations
        WHERE subject_id = %s AND capability = 'daily_ingestion'
          AND operating_state = 'active'
          AND effective_from <= %s
          AND (effective_to IS NULL OR effective_to >= %s)
        ORDER BY effective_from DESC LIMIT 1
        """,
        (account_id, as_of, as_of),
    ).fetchone()
    if registration is None or registration["source"] != "ibkr_flex":
        latest_import = connection.execute(
            """
            SELECT run_id, status, requested_to, completed_at
            FROM ingestion_runs
            WHERE run_type = 'staging-snapshot-import'
            ORDER BY run_id DESC LIMIT 1
            """
        ).fetchone()
        return {"latest_import": None if latest_import is None else dict(latest_import)}
    source = connection.execute(
        """
        SELECT q.flex_source_id, q.schedule_time_zone, q.schedule_hour, q.schedule_minute
        FROM subject_flex_source_bindings b
        JOIN ibkr_flex_queries q
          ON q.tenant_id = b.tenant_id AND q.flex_source_id = b.flex_source_id
        WHERE b.subject_id = %s AND b.is_primary AND b.status = 'active'
          AND b.effective_from <= %s
          AND (b.effective_to IS NULL OR b.effective_to >= %s)
        ORDER BY b.effective_from DESC LIMIT 1
        """,
        (account_id, as_of, as_of),
    ).fetchone()
    latest_import = None
    if source is not None:
        pull = connection.execute(
            """
            SELECT payload_json -> 'result' ->> 'ingestion_run_id' AS run_id,
                   status, payload_json ->> 'run_date' AS requested_to, completed_at
            FROM institutional_work_items
            WHERE work_kind = 'flex_statement_request' AND flex_source_id = %s
              AND status IN ('succeeded', 'failed', 'dead_letter', 'cancelled')
            ORDER BY completed_at DESC LIMIT 1
            """,
            (source["flex_source_id"],),
        ).fetchone()
        if pull is not None:
            latest_import = {
                **dict(pull),
                "status": "success" if pull["status"] == "succeeded" else pull["status"],
            }
    # The run that last wrote the account's newest NAV row.
    latest_nav_run = connection.execute(
        """
        SELECT n.snapshot_date, r.run_id, r.run_type, r.status, r.completed_at
        FROM nav_snapshots n
        JOIN ingestion_runs r ON r.run_id = n.last_seen_run_id
        WHERE n.account_id = %s
        ORDER BY n.snapshot_date DESC, (upper(n.asset_class) = 'TOTAL') DESC
        LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    return {
        "daily_feed": "ibkr_flex",
        "daily_feed_effective_from": registration["effective_from"],
        "daily_feed_source": None if source is None else {
            "flex_source_id": str(source["flex_source_id"]),
            "schedule_time_zone": source["schedule_time_zone"],
            "schedule_hour": source["schedule_hour"],
            "schedule_minute": source["schedule_minute"],
        },
        "latest_import": latest_import,
        "latest_nav_run": None if latest_nav_run is None else dict(latest_nav_run),
    }


def first_flex_pull_due(
    state: dict[str, Any], *, today_hkt: date, now_utc: datetime | None, grace_minutes: int = 60
) -> datetime | None:
    """On the switch day, until v2's first Flex pull is due, when it is due.

    That is: the Flex registration became effective yesterday (the first
    previous-day pull runs today), no pull of its source has finished today,
    and now_utc is before the source's scheduled time plus grace_minutes.
    Returns None otherwise, and always when now_utc is None.
    """
    if now_utc is None or state.get("daily_feed") != "ibkr_flex":
        return None
    effective = _date_value(state.get("daily_feed_effective_from"))
    if effective is None or effective + timedelta(days=1) != today_hkt:
        return None
    finished = _hkt_date((state.get("latest_import") or {}).get("completed_at"))
    if finished is not None and finished >= today_hkt:
        return None
    source = state.get("daily_feed_source") or {}
    try:
        scheduled = datetime.combine(
            today_hkt,
            time(int(source["schedule_hour"]), int(source["schedule_minute"])),
            tzinfo=ZoneInfo(str(source["schedule_time_zone"])),
        )
    except (KeyError, TypeError, ValueError):
        return None
    deadline = scheduled + timedelta(minutes=grace_minutes)
    return deadline if now_utc < deadline else None


def flex_import_failures(
    state: dict[str, Any], *, today_hkt: date, maximum_age_days: int = 0
) -> list[str]:
    """Checks for an account fed by v2's own Flex pull.

    The newest finished pull of the account's source must have succeeded and
    finished within maximum_age_days HKT days (0: today), and the account's
    newest NAV row must have been written by an institutional-flex-daily run
    in that window.
    """
    failures: list[str] = []
    window = "the current HKT date" if maximum_age_days == 0 else f"the last {maximum_age_days + 1} HKT days"

    def current(run: dict[str, Any]) -> bool:
        finished = _hkt_date(run.get("completed_at"))
        return finished is not None and 0 <= (today_hkt - finished).days <= maximum_age_days

    latest = state.get("latest_import") or {}
    if latest.get("status") != "success":
        failures.append("Latest institutional Flex import is not successful")
    elif not current(latest):
        failures.append(f"Institutional Flex import has not completed for {window}")
    nav_run = state.get("latest_nav_run") or {}
    if (
        nav_run.get("run_type") != FLEX_DAILY_RUN_TYPE
        or nav_run.get("status") != "success"
        or not current(nav_run)
    ):
        failures.append(f"Latest NAV row was not loaded by an institutional Flex import within {window}")
    return failures


def freshness_check(
    label: str, value: Any, *, today_hkt: date, maximum_age_days: int
) -> tuple[dict[str, Any], str | None]:
    observed = _date_value(value)
    result = {
        "label": label,
        "observed_date": None if observed is None else observed.isoformat(),
        "maximum_age_days": maximum_age_days,
    }
    if observed is None:
        return result, f"{label} date is unavailable or invalid"
    age = (today_hkt - observed).days
    result["age_days"] = age
    if age < 0:
        return result, f"{label} date is in the future"
    if age > maximum_age_days:
        return result, f"{label} is stale by {age} calendar days"
    return result, None


def portal_boundary_check(
    status_code: int,
    location: str | None,
    *,
    tenant_id: str,
) -> tuple[dict[str, Any], str | None]:
    parsed = urlparse(location or "")
    expected_path = f"/{tenant_id}/oauth2/v2.0/authorize"
    result = {
        "status_code": status_code,
        "redirect_host": parsed.hostname,
        "redirect_path": parsed.path,
    }
    if status_code == 401:
        result["boundary_mode"] = "server-to-server-denial"
        return result, None
    if status_code != 302:
        return result, (
            f"Portal returned HTTP {status_code}, expected an Entra 302 redirect "
            "or server-to-server 401 denial"
        )
    if parsed.hostname != "login.microsoftonline.com" or not parsed.path.startswith(
        expected_path
    ):
        return result, "Portal did not redirect to the approved single-tenant Entra endpoint"
    result["boundary_mode"] = "browser-redirect"
    return result, None


def evaluate_database_state(
    state: dict[str, Any], *, today_hkt: date
) -> tuple[dict[str, Any], list[str]]:
    checks: dict[str, Any] = {}
    failures: list[str] = []
    for label, key, maximum_age in (
        ("U16088571 NAV", "latest_nav_date", 4),
        ("S&P 500 benchmark", "latest_gspc_date", 4),
        ("NASDAQ Composite benchmark", "latest_ixic_date", 4),
        ("U.S. Treasury risk-free rate", "latest_risk_free_date", 7),
    ):
        result, failure = freshness_check(
            label,
            state.get(key),
            today_hkt=today_hkt,
            maximum_age_days=maximum_age,
        )
        checks[key] = result
        if failure:
            failures.append(failure)

    latest_import = state.get("latest_import") or {}
    checks["latest_import"] = latest_import
    if state.get("daily_feed") == "ibkr_flex":
        checks["daily_feed"] = "ibkr_flex"
        checks["latest_nav_run"] = state.get("latest_nav_run") or {}
        failures.extend(flex_import_failures(state, today_hkt=today_hkt))
    elif latest_import.get("status") != "success":
        failures.append("Latest account-only portal import is not successful")

    latest_snapshot = state.get("latest_snapshot") or {}
    checks["latest_snapshot"] = latest_snapshot
    if latest_snapshot.get("status") not in {"frozen", "approved"}:
        failures.append("Latest month-end snapshot is not frozen")
    if latest_snapshot.get("quality_status") == "fail":
        failures.append("Latest month-end snapshot has failed quality")

    ready_types = set(state.get("ready_report_types") or [])
    checks["ready_report_types"] = sorted(ready_types)
    if "factsheet" not in ready_types:
        failures.append("Latest month-end snapshot does not have a ready factsheet")

    client_approvals = int(state.get("client_approval_count") or 0)
    checks["client_approval_count"] = client_approvals
    if client_approvals:
        failures.append("Client release approval exists before Bloomberg and disclosure approval")

    assurance = state.get("portfolio_analyst_assurance") or {}
    checks["portfolio_analyst_assurance"] = assurance
    if assurance.get("status") != "pass":
        failures.append("Latest daily portfolio-analyst assurance did not pass")
    assurance_date = _date_value(assurance.get("assurance_date"))
    if assurance_date != today_hkt:
        failures.append("Daily portfolio-analyst assurance has not completed for the current HKT date")
    return checks, failures


def now_hkt_date() -> date:
    return datetime.now(HKT).date()


def consecutive_pass_days(
    results: list[dict[str, Any]], *, through_date: date
) -> int:
    """Count uninterrupted daily passes ending on through_date."""
    expected = through_date
    count = 0
    for row in sorted(results, key=lambda item: str(item["result_date"]), reverse=True):
        observed = _date_value(row.get("result_date"))
        if observed != expected or str(row.get("status")).lower() != "pass":
            break
        count += 1
        expected -= timedelta(days=1)
    return count


def burn_in_completion_date(start_date: date, required_days: int = 14) -> date:
    if required_days < 1:
        raise ValueError("required_days must be positive")
    return start_date + timedelta(days=required_days - 1)
