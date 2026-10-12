"""Trusted one-report website publication; no email, source writes or model calls."""
import hashlib
import json
import os
from datetime import date
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_report_runtime import validate_database_url,bind_report_session,verify_report_access,TENANT
from ibkr_etl.specialist_archive_reader import managed_reporting_archive
from ibkr_etl.specialist_evidence import RegisteredEvidenceReader
from ibkr_etl.agent_daily_report import prepare_daily_report
from ibkr_etl.agent_daily_inbox import publish_website_report,get_daily_report


def delivery_rows(c,day):
    return c.execute('''SELECT channel,state,attempts,provider_reference FROM specialist_report_deliveries
        WHERE tenant_id=%s AND report_date=%s ORDER BY channel''',(TENANT,day)).fetchall()


def main():
    day=date.fromisoformat(REPORT_DATE)
    target=os.environ['SPECIALIST_REPORT_DATABASE_URL'];validate_database_url(target)
    archive=RegisteredEvidenceReader(managed_reporting_archive())
    with psycopg.connect(target,autocommit=True,connect_timeout=15,row_factory=dict_row) as c:
        bind_report_session(c);access=verify_report_access(c)
        preview=prepare_daily_report(c,archive,tenant_id=TENANT,report_date=day,persist=False)
        if preview['report_sha256']!=REPORT_SHA256:
            raise ValueError('Independent protected report verification differs')
        rows=delivery_rows(c,day)
        if rows != [dict(channel=ch,state='pending',attempts=0,provider_reference=None) for ch in ('email','website')]:
            raise ValueError('Publication requires the exact untouched delivery pair')
        publication=publish_website_report(c,tenant_id=TENANT,report_date=day,report_sha256=REPORT_SHA256)
        if publication.get('state')!='available_on_website': raise ValueError('No website publication confirmed')
    # A second connection verifies committed availability and intact email state.
    with psycopg.connect(target,autocommit=True,connect_timeout=15,row_factory=dict_row) as c:
        bind_report_session(c);verify_report_access(c)
        detail=get_daily_report(c,tenant_id=TENANT,report_date=day)
        if not detail or detail['report_sha256']!=REPORT_SHA256 or detail['owner_read_confirmed'] is not False:
            raise ValueError('Fresh-login committed publication unavailable')
        message_hash=hashlib.sha256(detail['text'].encode()).hexdigest()
        if message_hash!=MESSAGE_SHA256: raise ValueError('Published message differs from the verified build')
        if delivery_rows(c,day)!=[dict(channel='email',state='pending',attempts=0,provider_reference=None),
                dict(channel='website',state='delivered',attempts=1,provider_reference='website:'+REPORT_SHA256)]:
            raise ValueError('Committed website or unchanged email state differs')
    archive.check_ready()
    print('SPECIALIST_DAILY_PUBLICATION '+json.dumps(dict(status='passed',**access,
        report_date=REPORT_DATE,report_sha256=REPORT_SHA256,message_sha256=message_hash,
        protected_report_reverified=True,fresh_login_readback_verified=True,
        website_published=True,owner_read_confirmed=False,email_submitted=False,
        messages_sent=0,source_rows_changed=0)),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_DAILY_PUBLICATION '+json.dumps(dict(status='failed',error_type=type(error).__name__)),flush=True)
        raise SystemExit(1)
