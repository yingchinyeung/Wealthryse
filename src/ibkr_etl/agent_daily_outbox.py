"""Durable daily delivery transitions. Caller commits before any external send.

This is not a mail sender or enabled schedule. A provider timeout after a send
is ambiguous: never blindly resend it or claim delivery without evidence.
"""
import hashlib
import uuid
from datetime import date

from .agent_daily_schedule import DELIVERY_CHANNELS, delivery_key, report_window
from .util import canonical_json


def require_tenant(connection, tenant_id):
    if connection.execute('SELECT app_tenant_id()::text AS tenant_id').fetchone()['tenant_id'] != tenant_id:
        raise ValueError('Daily report tenant mismatch')


def enqueue_report(connection, *, tenant_id, report_date: date, report: dict):
    require_tenant(connection, tenant_id)
    # Bind explicit dates/zone; catch-up must never label an old window as today.
    start, end = report_window(report_date)
    content = {'report_date': report_date.isoformat(), 'timezone': 'Asia/Hong_Kong',
               'period_start': start.isoformat(), 'period_end': end.isoformat(), 'report': report}
    encoded = canonical_json(content)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    connection.execute('''INSERT INTO specialist_daily_reports(tenant_id,report_date,report_sha256,report_json)
        VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING''', (tenant_id,report_date,digest,encoded))
    existing = connection.execute('''SELECT report_sha256,report_json FROM specialist_daily_reports
        WHERE tenant_id=%s AND report_date=%s''', (tenant_id,report_date)).fetchone()
    if not existing or existing['report_sha256'] != digest or canonical_json(existing['report_json']) != encoded:
        raise ValueError('Daily report content conflict; previous report retained')
    for channel in DELIVERY_CHANNELS:
        connection.execute('''INSERT INTO specialist_report_deliveries(tenant_id,report_date,channel,available_at)
            VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING''', (tenant_id,report_date,channel,end))
    return digest


def claim_delivery(connection, *, tenant_id, channel, lease_seconds=120):
    require_tenant(connection, tenant_id)
    if channel not in DELIVERY_CHANNELS or type(lease_seconds) is not int or not 15 <= lease_seconds <= 900:
        raise ValueError('Registered channel and bounded lease required')
    connection.execute('''UPDATE specialist_report_deliveries SET state='failed',lease_token=NULL,
        lease_expires_at=NULL,last_error_code='lease_attempts_exhausted',updated_at=clock_timestamp()
        WHERE tenant_id=%s AND channel=%s AND attempts>=8
          AND (state='pending' OR (state='leased' AND lease_expires_at<clock_timestamp()))''', (tenant_id,channel))
    token = str(uuid.uuid4())
    row = connection.execute('''WITH candidate AS (
        SELECT tenant_id,report_date,channel FROM specialist_report_deliveries
        WHERE tenant_id=%s AND channel=%s AND available_at<=clock_timestamp() AND attempts<8
          AND (state='pending' OR (state='leased' AND lease_expires_at<clock_timestamp()))
        ORDER BY report_date FOR UPDATE SKIP LOCKED LIMIT 1)
        UPDATE specialist_report_deliveries d SET state='leased',lease_token=%s,
          lease_expires_at=clock_timestamp()+(%s * interval '1 second'),attempts=attempts+1,updated_at=clock_timestamp()
        FROM candidate c WHERE d.tenant_id=c.tenant_id AND d.report_date=c.report_date AND d.channel=c.channel
        RETURNING d.*''', (tenant_id,channel,token,lease_seconds)).fetchone()
    if row:
        row = dict(row)
        row['delivery_key'] = delivery_key(tenant_id,row['report_date'],channel)
    return row


def start_send(connection, *, tenant_id, report_date, channel, lease_token):
    require_tenant(connection, tenant_id)
    row = connection.execute('''UPDATE specialist_report_deliveries SET state='sending',updated_at=clock_timestamp()
        WHERE tenant_id=%s AND report_date=%s AND channel=%s AND state='leased'
          AND lease_token=%s AND lease_expires_at>clock_timestamp() RETURNING channel''',
        (tenant_id,report_date,channel,lease_token)).fetchone()
    if not row:
        raise ValueError('Delivery lease expired or changed; send forbidden')


def finish_send(connection, *, tenant_id, report_date, channel, lease_token, outcome,
                provider_reference=None, error_code=None):
    require_tenant(connection, tenant_id)
    if outcome not in {'accepted','delivered','uncertain','failed'}:
        raise ValueError('Explicit provider outcome required')
    if outcome in {'accepted','delivered'} and (not isinstance(provider_reference,str) or not 1 <= len(provider_reference) <= 256):
        raise ValueError('Provider evidence required; acceptance is not delivery')
    row = connection.execute('''UPDATE specialist_report_deliveries SET state=%s,provider_reference=%s,
        last_error_code=%s,lease_token=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
        WHERE tenant_id=%s AND report_date=%s AND channel=%s AND state='sending' AND lease_token=%s
        RETURNING channel''', (outcome,provider_reference,error_code,tenant_id,report_date,channel,lease_token)).fetchone()
    if not row:
        raise ValueError('Delivery attempt changed; reconcile rather than overwrite')


def expire_ambiguous_sends(connection, *, tenant_id):
    require_tenant(connection, tenant_id)
    return connection.execute('''UPDATE specialist_report_deliveries SET state='uncertain',
        lease_token=NULL,lease_expires_at=NULL,last_error_code='send_ack_missing',updated_at=clock_timestamp()
        WHERE tenant_id=%s AND state='sending' AND lease_expires_at<=clock_timestamp() RETURNING report_date,channel''',
        (tenant_id,)).fetchall()
