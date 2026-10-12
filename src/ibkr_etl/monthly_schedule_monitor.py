"""Monthly dispatch telemetry; never retries, approves or certifies useful work.

The log-alert observation window is the first 48 hours after the due time.
An alert closing when that window ends does NOT prove the monthly job recovered.
"""
from datetime import datetime, timedelta, timezone

JOB = "ibkrv2stg-autonomy-review"
CRON = "0 1 1 * *"
OFFSET_MINUTES = 60
GRACE_MINUTES = 15


def monthly_issue(now: datetime, runtime: int, starts: int, terminals: int) -> str | None:
    if now.tzinfo is None or not 1 <= runtime <= 360 or min(starts, terminals) < 0:
        raise ValueError("Invalid monthly watchdog inputs")
    clock = now.astimezone(timezone.utc)
    due = clock.replace(day=1, hour=1, minute=0, second=0, microsecond=0)
    if not due + timedelta(minutes=15) <= clock < due + timedelta(days=2) or terminals:
        return None
    if not starts:
        return "MissingMonthlyStartTelemetry"
    if clock >= due + timedelta(minutes=runtime + 15):
        return "MissingMonthlyTerminalTelemetry"
    return None


def monthly_query(runtime: int) -> str:
    if type(runtime) is not int or not 1 <= runtime <= 360:
        raise ValueError("Bounded runtime minutes required")
    return f"""let Clock = now();
let Due = startofmonth(Clock) + 60m;
let Expected = datatable(JobName:string)['{JOB}']
| where Clock >= Due + 15m and Clock < Due + 2d
| extend ExecutionName = strcat(JobName, '-', tostring(datetime_diff('minute', Due, datetime(1970-01-01))));
let Events = ContainerAppSystemLogs_CL
| where TimeGenerated >= Due and TimeGenerated <= Clock
| where Reason_s in ('ContainerStarted', 'Completed', 'BackoffLimitExceeded', 'DeadlineExceeded')
| summarize Starts=countif(Reason_s == 'ContainerStarted'),
    Terminals=countif(Reason_s in ('Completed', 'BackoffLimitExceeded', 'DeadlineExceeded'))
    by JobName=JobName_s, ExecutionName=ExecutionName_s;
Expected
| join kind=leftouter Events on JobName, ExecutionName
| extend Starts=coalesce(Starts, 0), Terminals=coalesce(Terminals, 0)
| extend Issue=case(Terminals > 0, '', Starts == 0, 'MissingMonthlyStartTelemetry',
    Clock >= Due + ({runtime} + 15) * 1m, 'MissingMonthlyTerminalTelemetry', '')
| where isnotempty(Issue)
| project JobName, Issue, Due, ExecutionName, Starts, Terminals
"""
