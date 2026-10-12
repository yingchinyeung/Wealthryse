"""Deterministic daily publication; no models, mailbox sends or source access."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .agent_daily_report import prepare_daily_report
from .agent_daily_inbox import get_daily_report, publish_website_report
from .agent_daily_outbox import require_tenant
from .agent_report_notifications import ready_event, serialize_ready_event
from .specialist_report_runtime import TENANT, bind_report_session, verify_report_access

FIRST_REPORT_DATE = date(2026, 10, 3)
MAX_CATCH_UP = 7


def latest_closed_day(now):
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError('Aware server time required')
    local = now.astimezone(ZoneInfo('Asia/Hong_Kong'))
    return local.date() if local.hour >= 18 else local.date() - timedelta(days=1)


def pending_dates(connection, *, now, first_report_date=FIRST_REPORT_DATE, limit=MAX_CATCH_UP):
    """Outbox completion is the durable cursor; holes cannot be skipped by MAX()."""
    if type(first_report_date) is not date or type(limit) is not int or not 1 <= limit <= MAX_CATCH_UP:
        raise ValueError('Exact start date and bounded catch-up required')
    require_tenant(connection, TENANT)
    last = latest_closed_day(now)
    if last < first_report_date:
        return []
    rows = connection.execute('''SELECT days.day::date AS report_date
        FROM generate_series(%s::date, %s::date, interval '1 day') AS days(day)
        LEFT JOIN specialist_report_deliveries d ON d.tenant_id=%s
            AND d.report_date=days.day::date AND d.channel='website'
        WHERE d.state IS DISTINCT FROM 'delivered'
        ORDER BY days.day LIMIT %s''', (first_report_date, last, TENANT, limit)).fetchall()
    days = [r['report_date'] for r in rows]
    if (any(type(day) is not date or not first_report_date <= day <= last for day in days)
            or days != sorted(set(days)) or len(days) > limit):
        raise ValueError('Invalid durable reporting cursor')
    return days


def publish_exact(connection, archive, day):
    """Replays are checked, not replaced; only this date/hash may publish."""
    preview = prepare_daily_report(connection, archive, tenant_id=TENANT, report_date=day, persist=False)
    built = prepare_daily_report(connection, archive, tenant_id=TENANT, report_date=day, persist=True)
    if preview['report_sha256'] != built['report_sha256']:
        raise ValueError('Report changed between protected preview and commit')
    existing = get_daily_report(connection, tenant_id=TENANT, report_date=day)
    if existing is None:
        publication = publish_website_report(connection, tenant_id=TENANT,
            report_date=day, report_sha256=built['report_sha256'])
        if publication.get('state') != 'available_on_website' or publication.get('report_date') != day.isoformat():
            raise ValueError('Exact publication did not complete')
    elif existing['report_sha256'] != built['report_sha256']:
        raise ValueError('Existing immutable publication differs')
    return dict(report_date=day.isoformat(), report_sha256=built['report_sha256'],
                event_count=built['event_count'], already_published=existing is not None)


def run_daily_cycle(connect, archive, *, replay_date=None, emit=None):
    """A server clock, tenant lease and fresh login guard each execution.

    replay_date is a dispatcher-injected rehearsal only. Production supplies
    no browser-selected dates. No channel is allowed to submit email directly.
    """
    if replay_date is not None and type(replay_date) is not date:
        raise ValueError('Exact rehearsal date required')
    if emit is None:
        emit = lambda line: None
    with connect() as coordinator:
        bind_report_session(coordinator)
        access = verify_report_access(coordinator)
        locked = coordinator.execute("SELECT pg_try_advisory_lock(hashtextextended('specialist-daily-worker:' || %s, 0)) AS locked",
                                     (TENANT,)).fetchone()['locked']
        if locked is not True:
            return dict(status='busy', source_rows_changed=0, email_submitted=False)
        try:
            now = coordinator.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            latest = latest_closed_day(now)
            if replay_date is not None and replay_date > latest:
                raise ValueError('Rehearsal window has not closed')
            days = [replay_date] if replay_date is not None else pending_dates(coordinator, now=now)
            archive.check_ready()
            results = []
            for day in days:
                # Publication completes before this connection closes. No
                # telemetry can make an uncommitted transaction look complete.
                with connect() as worker:
                    bind_report_session(worker)
                    verify_report_access(worker)
                    item = publish_exact(worker, archive, day)
                with connect() as verifier:
                    bind_report_session(verifier)
                    verify_report_access(verifier)
                    report = get_daily_report(verifier, tenant_id=TENANT, report_date=day)
                    if report is None or report['report_sha256'] != item['report_sha256']:
                        raise ValueError('Fresh-login publication verification failed')
                    archive.check_ready()
                    item['fresh_login_readback_verified'] = True
                results.append(item)
            # Rehearsals do not emit outward readiness events. Catch-up of old
            # dates does not send a stale or falsely relabelled notification.
            with connect() as verifier:
                bind_report_session(verifier)
                verify_report_access(verifier)
                observed = verifier.execute('SELECT clock_timestamp() AS now').fetchone()['now']
                latest = latest_closed_day(observed)
                current = get_daily_report(verifier, tenant_id=TENANT, report_date=latest)
                remaining = pending_dates(verifier, now=observed)
                archive.check_ready()
                if replay_date is None and latest >= FIRST_REPORT_DATE and current is not None:
                    event = ready_event(verifier, tenant_id=TENANT, report_date=latest,
                                        now=observed, website_release_verified=True)
                    emit(serialize_ready_event(event))
            return dict(status='passed', **access, mode='rehearsal' if replay_date else 'scheduled',
                processed=results, current_report_date=latest.isoformat(),
                current_report_available=current is not None, catch_up_pending=len(remaining),
                website_release_verified=True, email_submitted=False, messages_sent=0,
                source_rows_changed=0, coverage='specialist-ledger-only; complete-system capture not established')
        finally:
            coordinator.execute("SELECT pg_advisory_unlock(hashtextextended('specialist-daily-worker:' || %s, 0))", (TENANT,))
