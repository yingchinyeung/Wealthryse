"""Transactional website availability and read-only daily-report inbox.

Caller must freshly authorize the database owner before exposing any result.
Website delivered means available in the inbox, not that a person has read it.
"""
import hashlib
import re
from datetime import date
from .agent_daily_outbox import claim_delivery, start_send, finish_send, require_tenant
from .agent_daily_report import render_daily_message
from .util import canonical_json


def verified_content(row):
    if not row or hashlib.sha256(canonical_json(row['report_json']).encode()).hexdigest()!=row['report_sha256']:
        raise ValueError('Daily report integrity check failed')
    if row['report_json']['report_date']!=row['report_date'].isoformat():
        raise ValueError('Daily report date mismatch')
    return render_daily_message(row['report_json'])


def publish_website_report(connection, *, tenant_id, report_date=None, report_sha256=None):
    if not connection.autocommit or connection.info.transaction_status!=0:
        raise ValueError('Idle dedicated autocommit publication connection required')
    if (report_date is None) != (report_sha256 is None):
        raise ValueError('Exact publication date and hash must be supplied together')
    if report_date is not None and (type(report_date) is not date or
            type(report_sha256) is not str or not re.fullmatch('[0-9a-f]{64}', report_sha256)):
        raise ValueError('Canonical publication date and hash required')
    with connection.transaction():
        lease=claim_delivery(connection,tenant_id=tenant_id,channel='website')
        if lease is None: return {'state':'idle'}
        # A controlled manual release may never publish a different queued day.
        # A mismatch raises inside the transaction, rolling back the lease too.
        if report_date is not None and lease['report_date'] != report_date:
            raise ValueError('A different report is first in the publication queue')
        row=connection.execute('''SELECT report_date,report_sha256,report_json
            FROM specialist_daily_reports WHERE tenant_id=%s AND report_date=%s''',
            (tenant_id,lease['report_date'])).fetchone()
        verified_content(row)
        if report_sha256 is not None and row['report_sha256'] != report_sha256:
            raise ValueError('The queued report differs from the approved publication hash')
        fields=dict(tenant_id=tenant_id,report_date=lease['report_date'],channel='website',lease_token=lease['lease_token'])
        start_send(connection,**fields)
        finish_send(connection,**fields,outcome='delivered',provider_reference='website:'+row['report_sha256'])
    return {'state':'available_on_website','report_date':lease['report_date'].isoformat(),
            'owner_read_confirmed':False}


def list_daily_reports(connection, *, tenant_id, limit=30):
    require_tenant(connection,tenant_id)
    if type(limit) is not int or not 1<=limit<=90: raise ValueError('Bounded report page required')
    rows=connection.execute('''SELECT r.report_date,r.report_sha256,d.updated_at AS published_at,
        e.state AS email_state FROM specialist_daily_reports r
        JOIN specialist_report_deliveries d ON d.tenant_id=r.tenant_id AND d.report_date=r.report_date
            AND d.channel='website' AND d.state='delivered'
        LEFT JOIN specialist_report_deliveries e ON e.tenant_id=r.tenant_id AND e.report_date=r.report_date AND e.channel='email'
        WHERE r.tenant_id=%s ORDER BY r.report_date DESC LIMIT %s''',(tenant_id,limit)).fetchall()
    return [{'report_date':r['report_date'].isoformat(),'report_sha256':r['report_sha256'],
             'published_at':r['published_at'].isoformat(),'website_state':'available',
             'email_state':r['email_state'] or 'not_queued'} for r in rows]


def get_daily_report(connection, *, tenant_id, report_date: date):
    require_tenant(connection,tenant_id)
    row=connection.execute('''SELECT r.report_date,r.report_sha256,r.report_json FROM specialist_daily_reports r
        JOIN specialist_report_deliveries d ON d.tenant_id=r.tenant_id AND d.report_date=r.report_date
            AND d.channel='website' AND d.state='delivered'
        WHERE r.tenant_id=%s AND r.report_date=%s''',(tenant_id,report_date)).fetchone()
    if row is None: return None
    message=verified_content(row)
    return {'report_date':report_date.isoformat(),'report_sha256':row['report_sha256'],
            'title':message['subject'],'text':message['text'],'owner_read_confirmed':False}
