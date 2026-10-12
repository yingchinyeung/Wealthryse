"""Commit-before-send daily email dispatcher with ambiguous-send handling.

The deployment injects a trusted, scoped sender; models cannot choose it or
recipients. Provider acceptance is not recipient delivery. No retry loop here.
"""
import hashlib
import re

from .agent_daily_outbox import claim_delivery, start_send, finish_send, expire_ambiguous_sends
from .agent_daily_report import render_daily_message
from .util import canonical_json


def dispatch_daily_email(connection, sender, *, tenant_id: str) -> dict:
    if not connection.autocommit or connection.info.transaction_status != 0:
        raise ValueError('Idle dedicated autocommit delivery connection required')
    # A deliberately disabled transport must not consume a queued report or
    # turn it into an uncertain send. Configuration is not permission proof.
    check_configuration = getattr(sender, 'assert_configured', None)
    if callable(check_configuration):
        check_configuration()
    with connection.transaction():
        expire_ambiguous_sends(connection,tenant_id=tenant_id)
        lease=claim_delivery(connection,tenant_id=tenant_id,channel='email',lease_seconds=120)
        if lease is None:
            return {'state':'idle','send_attempts':0}
        row=connection.execute('''SELECT report_sha256,report_json FROM specialist_daily_reports
            WHERE tenant_id=%s AND report_date=%s''',(tenant_id,lease['report_date'])).fetchone()
        if not row or hashlib.sha256(canonical_json(row['report_json']).encode()).hexdigest()!=row['report_sha256']:
            raise ValueError('Stored report hash mismatch; send forbidden')
        if row['report_json']['report_date']!=lease['report_date'].isoformat():
            raise ValueError('Delivery report date mismatch')
        message=render_daily_message(row['report_json'])
    fields=dict(tenant_id=tenant_id,report_date=lease['report_date'],channel='email',lease_token=lease['lease_token'])
    # The sending state must be committed before invoking the external service.
    # A crash after this point becomes uncertain, never an automatic resend.
    with connection.transaction():
        start_send(connection,**fields)
    try:
        receipt=sender.submit(message,delivery_key=lease['delivery_key'],report_sha256=row['report_sha256'])
        reference=receipt.get('provider_reference') if isinstance(receipt,dict) else None
        if (not isinstance(reference,str) or not re.fullmatch(r'[A-Za-z0-9._:-]{1,256}',reference)
                or receipt.get('state')!='accepted'):
            raise ValueError('No valid provider acceptance evidence')
    except Exception:
        # Do not retain exception bodies, message content, credentials or URLs.
        with connection.transaction():
            finish_send(connection,**fields,outcome='uncertain',error_code='provider_ack_unverified')
        return {'state':'uncertain','send_attempts':1,'recipient_delivery_confirmed':False}
    with connection.transaction():
        finish_send(connection,**fields,outcome='accepted',provider_reference=reference)
    return {'state':'accepted','send_attempts':1,'provider_reference':reference,
            'recipient_delivery_confirmed':False}
