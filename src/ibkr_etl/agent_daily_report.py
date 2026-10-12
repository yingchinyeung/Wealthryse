"""Build a closed daily window from the committed ledger and protected evidence.

Trusted worker only; no model, source values, email sending or execution permit.
Uses the same tenant writer lock as audit ingestion, and one transaction for
ledger boundaries, verification and immutable report/outbox publication.
"""
import hashlib
from datetime import date
from html import escape
from zoneinfo import ZoneInfo

from .agent_daily_outbox import enqueue_report, require_tenant
from .agent_daily_schedule import REPORT_EMAIL, REPORT_TIMEZONE, report_window
from .agent_governance import REGISTRY
from .specialist_autonomy import build_daily_activity_report
from .util import canonical_json


MAX_DAILY_EVENTS = 10000


def prepare_daily_report(connection, archive, *, tenant_id: str, report_date: date, persist=True) -> dict:
    if not connection.autocommit or connection.info.transaction_status != 0:
        raise ValueError("Idle dedicated autocommit reporting connection required")
    if type(persist) is not bool:
        raise ValueError("Explicit reporting persistence mode required")
    def check_archive():
        if archive is None or not callable(getattr(archive, 'check_ready', None)):
            raise ValueError("Protected archive readiness verification required")
        receipt = archive.check_ready()
        if not isinstance(receipt, dict) or receipt.get('protected_archive_verified') is not True:
            raise ValueError("Protected archive readiness not confirmed")
    def check_detail(event):
        verifier=getattr(archive,'verify_detail',None)
        if not callable(verifier):
            raise ValueError('Registered detailed evidence verifier required')
        receipt=verifier(tenant_id,event)
        if (not isinstance(receipt,dict) or receipt.get('detail_verified') is not True
            or receipt.get('evidence_sha256')!=event.get('evidence_sha256')):
            raise ValueError('Registered detailed evidence not confirmed')
    # An empty ledger must still prove live protected-storage access. No event
    # loop may silently turn a missing/unreachable archive into verification.
    check_archive()
    start, end = report_window(report_date)
    with connection.transaction():
        # Do not acquire a repeatable-read snapshot before an in-flight audit
        # writer finishes. The shared lock plus read committed freezes the
        # complete committed slice, including a writer that was ahead of us.
        connection.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED" + (" READ ONLY" if not persist else ""))
        require_tenant(connection, tenant_id)
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('specialist-audit:' || %s, 0))", (tenant_id,))
        now = connection.execute("SELECT clock_timestamp() AS observed_at").fetchone()['observed_at']
        if now < end:
            raise ValueError("Reporting period has not closed")
        boundaries = []
        for cutoff in (start, end):
            row = connection.execute("""SELECT sequence_no,event_sha256,event_json
                FROM specialist_audit_events WHERE tenant_id=%s
                AND (event_json->>'occurred_at')::timestamptz < %s
                ORDER BY sequence_no DESC LIMIT 1""", (tenant_id,cutoff)).fetchone()
            boundaries.append(row)
        prior, head = boundaries
        first = prior['sequence_no'] + 1 if prior else 1
        last = head['sequence_no'] if head else 0
        previous_hash = prior['event_sha256'] if prior else '0'*64
        last_hash = head['event_sha256'] if head else '0'*64
        if last < first - 1 or last - first + 1 > MAX_DAILY_EVENTS:
            raise ValueError("Daily ledger range exceeds verified bounds")
        # The previous boundary must be independently retained even on a day
        # without new actions. Never convert missing evidence to a success.
        if prior:
            archive.verify(tenant_id, prior['event_json'])
            check_detail(prior['event_json'])
        rows = connection.execute("""SELECT sequence_no,event_sha256,event_json
            FROM specialist_audit_events WHERE tenant_id=%s AND sequence_no BETWEEN %s AND %s
            ORDER BY sequence_no LIMIT %s""", (tenant_id,first,last,MAX_DAILY_EVENTS+1)).fetchall()
        events = []
        for row in rows:
            event = row['event_json']
            if event.get('sequence') != row['sequence_no'] or event.get('sha256') != row['event_sha256']:
                raise ValueError("Ledger row and event differ")
            archive.verify(tenant_id, event)
            check_detail(event)
            events.append(event)
        report = build_daily_activity_report(events, period_start=start,period_end=end,
            first_sequence=first,previous_sha256=previous_hash,last_sequence=last,last_sha256=last_hash)
        report.update(first_sequence=first,previous_sha256=previous_hash,
                      last_sequence=last,protected_archive_verified=True)
        check_archive()
        if not persist:
            content = dict(report_date=report_date.isoformat(),timezone=REPORT_TIMEZONE,
                           period_start=start.isoformat(),period_end=end.isoformat(),report=report)
            return {'report_sha256':hashlib.sha256(canonical_json(content).encode()).hexdigest(),
                    'content':content,'event_count':len(events),'report_date':report_date.isoformat(),
                    'queued_channels':[],'database_committed':False,'reports_created':0,
                    'messages_sent':0,'execution_authorized':False,'mode':'read_only_preview'}
        report_hash = enqueue_report(connection,tenant_id=tenant_id,report_date=report_date,report=report)
    return {'report_sha256':report_hash,'event_count':len(events),
            'report_date':report_date.isoformat(),'queued_channels':['website','email'],
            'database_committed':True,'messages_sent':0,'execution_authorized':False}


def render_daily_message(content: dict) -> dict:
    """Deterministic plain-text/escaped-HTML summary, fixed approved recipient.

    No arbitrary links, prompts, financial source values or unstructured event
    payloads enter the outgoing message. Channel submission is a separate step.
    """
    day = date.fromisoformat(content['report_date'])
    start,end = report_window(day)
    if (content['timezone'] != REPORT_TIMEZONE or content['period_start'] != start.isoformat()
            or content['period_end'] != end.isoformat()):
        raise ValueError("Report window mismatch")
    report = content['report']
    if report.get('ledger_slice_verified') is not True or report.get('protected_archive_verified') is not True:
        raise ValueError("Verified ledger and archive required")
    count = report['event_count']
    if type(count) is not int or not 0 <= count <= MAX_DAILY_EVENTS:
        raise ValueError("Invalid report count")
    roles = report['role_counts']
    if set(roles) != set(REGISTRY) or any(type(v) is not int or v < 0 for v in roles.values()) or sum(roles.values()) != count:
        raise ValueError("Invalid role counts")
    subject = f"Agent operations daily report — {day.isoformat()} (Hong Kong)"
    lines = [subject, '', f"Reporting period: {start.astimezone(ZoneInfo(REPORT_TIMEZONE)).isoformat()} to {end.astimezone(ZoneInfo(REPORT_TIMEZONE)).isoformat()}",
             f"Verified audit events: {count}", '']
    if count == 0:
        lines.append('No specialist actions were recorded in this reporting window.')
    lines.extend(f"{definition.label}: {roles[role]} audit events" for role,definition in REGISTRY.items())
    lines.extend(['', 'Coverage: recorded specialist-ledger events only; complete system-wide capture is not yet established.',
                  'An event is a recorded stage of work, not necessarily a completed task.',
                  'Recorded events may include trusted rollout checks; they do not prove autonomous specialist execution.',
                  'This report does not approve changes or release financial reports.'])
    plain = '\n'.join(lines)
    return {'recipient':REPORT_EMAIL,'subject':subject,'text':plain,
            'html':'<pre>'+escape(plain)+'</pre>'}
