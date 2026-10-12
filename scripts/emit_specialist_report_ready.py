"""Fresh restricted read of a published report, then metadata-only readiness."""
import json
import os
from datetime import date,datetime,timezone
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_report_runtime import validate_database_url,bind_report_session,verify_report_access,TENANT
from ibkr_etl.agent_report_notifications import ready_event,serialize_ready_event


def main():
    target=os.environ['SPECIALIST_REPORT_DATABASE_URL'];validate_database_url(target)
    with psycopg.connect(target,autocommit=True,connect_timeout=15,row_factory=dict_row) as c:
        bind_report_session(c);access=verify_report_access(c)
        event=ready_event(c,tenant_id=TENANT,report_date=date.fromisoformat(REPORT_DATE),
            now=datetime.now(timezone.utc),website_release_verified=True)
        if event['report_sha256']!=REPORT_SHA256: raise ValueError('Publication hash changed')
        line=serialize_ready_event(event)
    print(line,flush=True)
    print('SPECIALIST_READY_ACCEPTANCE '+json.dumps(dict(status='passed',**access,
        report_date=REPORT_DATE,report_sha256=REPORT_SHA256,notification_key=event['notification_key'],
        website_reverified=True,database_writes=0,email_submitted=False,source_rows_changed=0)),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_READY_ACCEPTANCE '+json.dumps(dict(status='failed',error_type=type(error).__name__)),flush=True)
        raise SystemExit(1)
