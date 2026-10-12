"""Deterministic publication monitoring, independent of models and email sends."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from .agent_daily_schedule import report_window
from .agent_daily_inbox import get_daily_report


def publication_readiness(connection, *, tenant_id, now, schedule_active=False, grace_minutes=15):
    """Do not label an intentionally disabled schedule as a missed report.

    Website availability requires a fresh hash-verified committed report. An
    email or model failure cannot turn missing/corrupt content into success.
    """
    if not isinstance(now,datetime) or now.utcoffset() is None:
        raise ValueError('Aware publication monitoring time required')
    if type(schedule_active) is not bool or type(grace_minutes) is not int or not 1<=grace_minutes<=60:
        raise ValueError('Typed schedule status and bounded grace required')
    if not connection.autocommit or connection.info.transaction_status!=0:
        raise ValueError('Fresh dedicated idle monitoring connection required')
    local=now.astimezone(ZoneInfo('Asia/Hong_Kong'))
    day=local.date() if local.hour>=18 else local.date()-timedelta(days=1)
    _,end=report_window(day)
    from .agent_daily_outbox import require_tenant
    require_tenant(connection,tenant_id)
    report=get_daily_report(connection,tenant_id=tenant_id,report_date=day)
    if report is not None:
        state='available'
    elif not schedule_active:
        state='schedule_disabled'
    elif now<end+timedelta(minutes=grace_minutes):
        state='awaiting_publication'
    else:
        state='publication_overdue'
    return dict(report_date=day.isoformat(),state=state,schedule_active=schedule_active,
        website_available=report is not None,recipient_delivery_confirmed=False,
        report_sha256=report['report_sha256'] if report else None,
        action_required=state=='publication_overdue')
