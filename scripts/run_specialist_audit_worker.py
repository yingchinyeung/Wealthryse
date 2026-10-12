"""Manual audit database-access check only. No event writer, HTTP ingress or schedule."""
import json
import os
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_audit_runtime import validate_database_url, bind_audit_session, verify_audit_access


def main():
    validate_database_url(os.environ['SPECIALIST_AUDIT_DATABASE_URL'])
    with psycopg.connect(os.environ['SPECIALIST_AUDIT_DATABASE_URL'],autocommit=True,connect_timeout=20,
                        row_factory=dict_row) as c:
        bind_audit_session(c)
        result=verify_audit_access(c)
    print('SPECIALIST_AUDIT_WORKER '+json.dumps(dict(result,status='passed',mode='verify',messages_sent=0)),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_AUDIT_WORKER '+json.dumps({'status':'failed','error_type':type(error).__name__,
              'reason':str(error) if isinstance(error,ValueError) else 'Audit preflight failed; inspect restricted evidence'}),flush=True)
        raise SystemExit(1)
