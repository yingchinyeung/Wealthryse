"""Fixed-date manual publication of one closed daily report and its outbox.

No website publication, email sender, model calls or source-data access. Date is
injected by the trusted dispatcher, never by a browser or specialist prompt.
"""
import hashlib
import json
import os
from datetime import date
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_report_runtime import validate_database_url,bind_report_session,verify_report_access,TENANT
from ibkr_etl.specialist_archive_reader import managed_reporting_archive
from ibkr_etl.specialist_evidence import RegisteredEvidenceReader,inspect_evidence_inventory
from ibkr_etl.agent_daily_report import prepare_daily_report,render_daily_message
from ibkr_etl.util import canonical_json


def check_stored(c, day, expected):
    row=c.execute('''SELECT report_sha256,report_json FROM specialist_daily_reports
        WHERE tenant_id=%s AND report_date=%s''',(TENANT,day)).fetchone()
    if (not row or row['report_sha256']!=expected
        or hashlib.sha256(canonical_json(row['report_json']).encode()).hexdigest()!=expected):
        raise ValueError('Stored immutable daily report differs')
    message=render_daily_message(row['report_json'])
    rows=c.execute('''SELECT channel,state,attempts FROM specialist_report_deliveries
        WHERE tenant_id=%s AND report_date=%s ORDER BY channel''',(TENANT,day)).fetchall()
    if rows!=[dict(channel='email',state='pending',attempts=0),dict(channel='website',state='pending',attempts=0)]:
        raise ValueError('Exact pending outbox required; no delivery may be claimed')
    return hashlib.sha256(message['text'].encode()).hexdigest()


def main():
    day=date.fromisoformat(REPORT_DATE)
    if day.isoformat()!=REPORT_DATE: raise ValueError('Canonical report date required')
    target=os.environ['SPECIALIST_REPORT_DATABASE_URL'];validate_database_url(target)
    archive=RegisteredEvidenceReader(managed_reporting_archive())
    with psycopg.connect(target,autocommit=True,connect_timeout=15,row_factory=dict_row) as c:
        bind_report_session(c);access=verify_report_access(c)
        inventory=inspect_evidence_inventory(c,archive)
        preview=prepare_daily_report(c,archive,tenant_id=TENANT,report_date=day,persist=False)
        # Exact replay checks the previously retained content, never replaces it.
        result=prepare_daily_report(c,archive,tenant_id=TENANT,report_date=day,persist=True)
        replay=prepare_daily_report(c,archive,tenant_id=TENANT,report_date=day,persist=True)
        if result!=replay or result['report_sha256']!=preview['report_sha256']:
            raise ValueError('Daily report preview/publication/replay differ')
        message_hash=check_stored(c,day,result['report_sha256'])
    # A fresh restricted login verifies committed bytes and pending delivery.
    with psycopg.connect(target,autocommit=True,connect_timeout=15,row_factory=dict_row) as c:
        bind_report_session(c);verify_report_access(c)
        if check_stored(c,day,result['report_sha256'])!=message_hash:
            raise ValueError('Fresh-login report verification differs')
    archive.check_ready()
    print('SPECIALIST_DAILY_BUILD '+json.dumps(dict(status='passed',**access,**inventory,
        **result,exact_replay_verified=True,fresh_login_readback_verified=True,
        pending_deliveries_verified=2,website_published=False,email_submitted=False,
        source_rows_changed=0,message_sha256=message_hash,
        coverage='specialist-ledger-only; complete-system capture not established')),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_DAILY_BUILD '+json.dumps(dict(status='failed',error_type=type(error).__name__)),flush=True)
        raise SystemExit(1)
