"""Manual read-only live report rehearsal. No publication, email or queue writes."""
import hashlib
import json
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_report_runtime import validate_database_url, bind_report_session, verify_report_access, TENANT
from ibkr_etl.specialist_archive_reader import managed_reporting_archive
from ibkr_etl.agent_daily_report import prepare_daily_report, render_daily_message
from ibkr_etl.specialist_evidence import RegisteredEvidenceReader,inspect_evidence_inventory


def main():
    validate_database_url(os.environ['SPECIALIST_REPORT_DATABASE_URL'])
    now = datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Hong_Kong'))
    day = now.date() if now.hour >= 18 else now.date()-timedelta(days=1)
    archive = RegisteredEvidenceReader(managed_reporting_archive())
    with psycopg.connect(os.environ['SPECIALIST_REPORT_DATABASE_URL'],autocommit=True,connect_timeout=20,
                        row_factory=dict_row) as c:
        bind_report_session(c)
        access = verify_report_access(c)
        inventory = inspect_evidence_inventory(c,archive)
        before = [c.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'] for table in
                  ('specialist_daily_reports','specialist_report_deliveries')]
        result = prepare_daily_report(c,archive,tenant_id=TENANT,report_date=day,persist=False)
        message = render_daily_message(result.pop('content'))
        after = [c.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'] for table in
                 ('specialist_daily_reports','specialist_report_deliveries')]
        if before != after:
            raise ValueError('Report inventory changed during read-only rehearsal; inspect concurrency')
    # No model prompts, event payloads, credentials or financial data are logged.
    result.update(access,**inventory,status='passed',archive_read_verified=True,report_inventory_unchanged=True,
                  message_sha256=hashlib.sha256(message['text'].encode()).hexdigest(),
                  coverage='specialist-ledger-only; complete-system capture not established')
    print('SPECIALIST_REPORT_PREVIEW '+json.dumps(result),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_REPORT_PREVIEW '+json.dumps({'status':'failed','error_type':type(error).__name__,
              'reason':str(error) if isinstance(error,ValueError) else 'Read-only report rehearsal failed'}),flush=True)
        raise SystemExit(1)
