"""Product daily-report windows. No Codex reminder or enabled cloud schedule."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

REPORT_TIMEZONE = "Asia/Hong_Kong"
REPORT_HOUR = 18
DELIVERY_CHANNELS = ("website", "email")
REPORT_EMAIL = "anthony.ying@wealthryse.ai"


def report_window(report_date: date) -> tuple[datetime, datetime]:
    end = datetime.combine(report_date, time(REPORT_HOUR), ZoneInfo(REPORT_TIMEZONE))
    return (end - timedelta(days=1)).astimezone(timezone.utc), end.astimezone(timezone.utc)


def due_report_dates(*, now: datetime, next_report_date: date, limit: int = 31) -> list[date]:
    """Catch up in order from a durable cursor; never silently skip missed days."""
    if now.utcoffset() is None or type(limit) is not int or not 1 <= limit <= 31:
        raise ValueError("Aware time and bounded catch-up limit required")
    result = []
    candidate = next_report_date
    while len(result) < limit and report_window(candidate)[1] <= now:
        result.append(candidate)
        candidate += timedelta(days=1)
    return result


def delivery_key(tenant_id: str, report_date: date, channel: str) -> str:
    """Stable key survives job retries; report content must be bound separately."""
    import uuid
    if str(uuid.UUID(tenant_id)) != tenant_id or channel not in DELIVERY_CHANNELS:
        raise ValueError("Exact tenant and registered channel required")
    return f"agent-daily-v1:{tenant_id}:{report_date.isoformat()}:{channel}"
