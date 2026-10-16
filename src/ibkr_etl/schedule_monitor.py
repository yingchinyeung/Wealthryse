"""Schedule-telemetry watchdog queries. No portfolio data, retries or job starts.

Scheduled execution suffixes were verified against Azure's actual system logs.
This is a monitored platform convention, not proof of successful ingestion.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

_DAILY_V1 = (("ibkr-v11-daily", 480, 60),)
_DAILY_V2 = (
    ("ibkrv2stg-account-sync-daily", 490, 30),
    ("ibkrv2stg-benchmark-daily", 510, 20),
    ("ibkrv2stg-sector-classification", 525, 30),
    ("ibkrv2stg-derived-daily", 530, 60),
    ("ibkrv2stg-risk-snapshots", 535, 60),
    ("ibkrv2stg-month-end-factsheet", 540, 60),
    ("ibkrv2stg-analyst-assurance", 550, 10),
    ("ibkrv2stg-burn-in-monitor", 555, 5),
    ("ibkrv2stg-institutional-daily", 500, 50),
    ("ibkrv2stg-risk-free-daily", 1380, 20),
)
# Stopped once v2 makes its own daily Flex pull.
RETIRED_BY_DIRECT_FLEX = ("ibkr-v11-daily", "ibkrv2stg-account-sync-daily")
DAILY_FEEDS = ("v11_export", "direct_flex")


def daily_schedules(feed: str | None = None) -> tuple[tuple, tuple]:
    """Expected daily jobs (v1.1 workspace, v2 workspace).

    IBKR_DAILY_FEED=direct_flex leaves out v1.1's pull and account-sync, which
    stop on the switch day. The default is today's v1.1-export feed.
    """
    chosen = (feed if feed is not None else os.environ.get("IBKR_DAILY_FEED", "v11_export")).strip().lower()
    if chosen not in DAILY_FEEDS:
        raise ValueError(f"Unsupported IBKR_DAILY_FEED: {chosen}")
    if chosen == "v11_export":
        return _DAILY_V1, _DAILY_V2
    return tuple(
        tuple(row for row in specs if row[0] not in RETIRED_BY_DIRECT_FLEX)
        for specs in (_DAILY_V1, _DAILY_V2)
    )


DAILY_V1, DAILY_V2 = daily_schedules()
HEARTBEATS = ("ibkrv2stg-autonomous-admin", "ibkrv2stg-flex-discovery")
GRACE_MINUTES = 15
TERMINAL_REASONS = ("Completed", "BackoffLimitExceeded", "DeadlineExceeded")


def due_daily(now: datetime, offset_minutes: int) -> datetime:
    if now.tzinfo is None or not 0 <= offset_minutes < 1440:
        raise ValueError("Timezone-aware UTC-convertible time and daily minute required")
    eligible = now.astimezone(timezone.utc) - timedelta(minutes=GRACE_MINUTES)
    due = eligible.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=offset_minutes)
    return due if due <= eligible else due - timedelta(days=1)


def execution_name(job: str, due: datetime) -> str:
    if not re.fullmatch(r"[a-z0-9-]+", job) or due.tzinfo is None:
        raise ValueError("Invalid job or time")
    return f"{job}-{int(due.timestamp() // 60)}"


def daily_issue(now: datetime, offset: int, runtime: int, starts: int, terminals: int) -> str | None:
    due = due_daily(now, offset)
    if terminals:
        return None  # Failed execution alerts are handled separately.
    if not starts:
        return "MissingScheduledStartTelemetry"
    if now >= due + timedelta(minutes=GRACE_MINUTES + runtime):
        return "MissingScheduledTerminalTelemetry"
    return None


def daily_query(specs: tuple, *, source: str = "ContainerAppSystemLogs_CL") -> str:
    rows = []
    for name, offset, runtime in specs:
        if not re.fullmatch(r"[a-z0-9-]+", name) or not 0 <= offset < 1440 or not 1 <= runtime <= 360:
            raise ValueError("Invalid schedule specification")
        rows.append(f"'{name}', {offset}, {runtime}")
    if not rows:
        raise ValueError("Missing daily schedules")
    return f"""let Eligible = now() - 15m;
let Expected = datatable(JobName:string, OffsetMinutes:long, RuntimeMinutes:long)[
{','.join(rows)}
]
| extend Candidate = startofday(Eligible) + OffsetMinutes * 1m
| extend Due = iff(Candidate <= Eligible, Candidate, Candidate - 1d)
| extend ExecutionName = strcat(JobName, '-', tostring(datetime_diff('minute', Due, datetime(1970-01-01))));
let Events = {source}
| where TimeGenerated > ago(2d)
| where Reason_s in ('ContainerStarted', 'Completed', 'BackoffLimitExceeded', 'DeadlineExceeded')
| summarize Starts=countif(Reason_s == 'ContainerStarted'),
    Terminals=countif(Reason_s in ('Completed','BackoffLimitExceeded','DeadlineExceeded'))
    by JobName=JobName_s, ExecutionName=ExecutionName_s;
Expected
| join kind=leftouter Events on JobName, ExecutionName
| extend Starts=coalesce(Starts, 0), Terminals=coalesce(Terminals, 0)
| extend Issue=case(Terminals > 0, '', Starts == 0, 'MissingScheduledStartTelemetry',
    now() >= Due + (RuntimeMinutes + 15) * 1m, 'MissingScheduledTerminalTelemetry', '')
| where isnotempty(Issue)
| project JobName, Issue, Due, ExecutionName, Starts, Terminals
"""


def heartbeat_query(*, source: str = "ContainerAppSystemLogs_CL") -> str:
    names = ",".join(f"'{name}'" for name in HEARTBEATS)
    return f"""let Expected = datatable(JobName:string)[{names}];
let Events = {source}
| where TimeGenerated > ago(1h)
| where ExecutionName_s matches regex @'-[0-9]+$'
| summarize LastStart=maxif(TimeGenerated, Reason_s == 'ContainerStarted'),
    LastTerminal=maxif(TimeGenerated, Reason_s in ('Completed','BackoffLimitExceeded','DeadlineExceeded'))
    by JobName=JobName_s;
Expected
| join kind=leftouter Events on JobName
| extend Issue=case(isnull(LastStart) or LastStart < ago(20m), 'BackgroundStartHeartbeatMissing',
    isnull(LastTerminal) or LastTerminal < ago(35m), 'BackgroundTerminalHeartbeatMissing', '')
| where isnotempty(Issue)
| project JobName, Issue, LastStart, LastTerminal
"""
