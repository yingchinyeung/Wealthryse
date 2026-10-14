from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse


HKT = timezone(timedelta(hours=8), name="Asia/Hong_Kong")


def _date_value(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


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
    if latest_import.get("status") != "success":
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
