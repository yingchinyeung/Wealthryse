"""Minimal Azure report-ready telemetry, separate from confirmed email delivery.

Only trusted workers may emit this after committed website publication and a
fresh integrity read. No mailbox authority, financial payloads or model text.
The Azure rule remains disabled until the website release is accepted.
"""
import hashlib
import json
from datetime import date, datetime, timedelta, timezone

from .agent_daily_inbox import get_daily_report
from .agent_daily_schedule import report_window

TENANT = '00000000-0000-0000-0000-000000000001'
JOB = 'ibkrv2stg-agent-report'
RULE = 'ibkr-v2-specialist-daily-report-ready'
PREFIX = 'SPECIALIST_REPORT_READY '
SCHEMA = 'specialist-report-ready-v1'
INBOX_URL = 'https://ibkrv2stg-web.purplerock-8cff24a2.eastasia.azurecontainerapps.io/ai-operations'


def ready_event(connection, *, tenant_id, report_date, now, website_release_verified=False):
    """Return bounded metadata; never imply an Azure/email receipt from stdout."""
    if website_release_verified is not True:
        raise ValueError('Accepted authenticated website release required')
    if tenant_id != TENANT or type(report_date) is not date or not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError('Registered tenant, exact date and aware time required')
    if not connection.autocommit or connection.info.transaction_status != 0:
        raise ValueError('Fresh dedicated idle reporting connection required')
    _, end = report_window(report_date)
    if not end <= now < end + timedelta(days=1):
        raise ValueError('Only the most recently closed daily report may notify')
    report = get_daily_report(connection, tenant_id=tenant_id, report_date=report_date)
    if report is None:
        raise ValueError('Committed published report required')
    key = hashlib.sha256(f'{SCHEMA}:{tenant_id}:{report_date.isoformat()}'.encode()).hexdigest()
    return dict(schema=SCHEMA, tenant_id=tenant_id, report_date=report_date.isoformat(),
                report_sha256=report['report_sha256'], notification_key=key,
                recorded_at=now.astimezone(timezone.utc).isoformat(), website_state='available',
                recipient_delivery_confirmed=False)


def serialize_ready_event(event):
    """Validate the exact schema before it can enter outward alert telemetry."""
    keys = {'schema','tenant_id','report_date','report_sha256','notification_key',
            'recorded_at','website_state','recipient_delivery_confirmed'}
    if not isinstance(event, dict) or set(event) != keys:
        raise ValueError('Exact minimal notification schema required')
    day = date.fromisoformat(event['report_date'])
    observed = datetime.fromisoformat(event['recorded_at'])
    _, end = report_window(day)
    expected_key = hashlib.sha256(f'{SCHEMA}:{TENANT}:{day.isoformat()}'.encode()).hexdigest()
    if (event['schema'] != SCHEMA or event['tenant_id'] != TENANT or day.isoformat() != event['report_date']
            or event['website_state'] != 'available' or event['recipient_delivery_confirmed'] is not False
            or event['notification_key'] != expected_key or observed.utcoffset() != timedelta(0)
            or not end <= observed < end + timedelta(days=1)
            or not isinstance(event['report_sha256'], str) or len(event['report_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in event['report_sha256'])):
        raise ValueError('Invalid ready notification')
    return PREFIX + json.dumps(event, sort_keys=True, separators=(',', ':'))


def ready_query():
    """Stateful per-date alerts suppress duplicate log copies within the window.

    This is telemetry deduplication, not exactly-once recipient delivery. Only
    fixed producer/tenant/schema/window metadata qualifies. Replay outside the
    report's following 24 hours does not create a new readiness notification.
    """
    return "\n".join([
        'ContainerAppConsoleLogs_CL',
        '| where TimeGenerated > ago(1d)',
        f"| where ContainerGroupName_s startswith '{JOB}-'",
        f"| where Log_s startswith '{PREFIX}'",
        f'| extend E = parse_json(substring(Log_s, {len(PREFIX)}))',
        f"| where tostring(E.schema) == '{SCHEMA}' and tostring(E.tenant_id) == '{TENANT}'",
        "| where tostring(E.website_state) == 'available' and gettype(E.recipient_delivery_confirmed) == 'bool' and E.recipient_delivery_confirmed == false",
        # parse_json auto-converts ISO date strings into dynamic datetimes.
        # Extract the canonical date as a string so hashing and period-end
        # construction use the exact original date, not an expanded timestamp.
        f'| extend ReportDate=extract_json("$.report_date", substring(Log_s, {len(PREFIX)}), typeof(string)), ReportHash=tostring(E.report_sha256), NotificationKey=tostring(E.notification_key), RecordedAt=todatetime(E.recorded_at)',
        '| extend PeriodEnd=todatetime(strcat(ReportDate, "T10:00:00Z"))',
        '| where ReportDate matches regex @"^\\d{4}-\\d{2}-\\d{2}$" and ReportHash matches regex @"^[0-9a-f]{64}$"',
        f'| where NotificationKey == hash_sha256(strcat("{SCHEMA}:{TENANT}:", ReportDate))',
        '| where RecordedAt >= PeriodEnd and RecordedAt < PeriodEnd + 1d',
        '| where now() >= PeriodEnd and now() < PeriodEnd + 1d and RecordedAt <= now() + 5m',
        '| summarize arg_max(TimeGenerated, ReportHash) by ReportDate, NotificationKey',
        '| project ReportDate, NotificationKey, ReportHash',
    ])


def rule_definition(*, workspace_id, action_group_id):
    """Dormant rule only: no caller-controlled enablement or destination."""
    base = '/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/'
    if (workspace_id != base + 'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs'
            or action_group_id != base + 'Microsoft.Insights/actionGroups/ibkr-v11-daily-alerts'):
        raise ValueError('Registered Azure notification destination required')
    return dict(location='eastasia', kind='LogAlert',
        tags=dict(application='ibkr-portfolio-platform', purpose='specialist-report-readiness', mode='dormant'),
        properties=dict(displayName='Agent daily report ready on the website',
            description='Informational report-ready notice, not a system failure. Full reports require owner sign-in. Azure notification submission is not confirmation of recipient delivery.',
            enabled=False, severity=3, scopes=[workspace_id], evaluationFrequency='PT5M',
            windowSize='PT5M', overrideQueryTimeRange='P1D', autoMitigate=True,
            skipQueryValidation=False,
            criteria=dict(allOf=[dict(query=ready_query(), timeAggregation='Count', operator='GreaterThan',
                threshold=0, dimensions=[dict(name='NotificationKey', operator='Include', values=['*'])],
                failingPeriods=dict(numberOfEvaluationPeriods=1, minFailingPeriodsToAlert=1))]),
            actions=dict(actionGroups=[action_group_id], customProperties=dict(
                Website=INBOX_URL, Purpose='Informational daily report ready',
                Privacy='No portfolio values, source documents or credentials included',
                Delivery='Recipient delivery is not confirmed by report-ready telemetry'))))
