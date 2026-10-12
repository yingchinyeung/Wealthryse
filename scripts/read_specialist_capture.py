"""Read-only report identity verifies capture and its protected details."""
import os
import json
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_report_runtime import validate_database_url
from ibkr_etl.specialist_archive_reader import managed_reporting_archive
from ibkr_etl.specialist_capture_verifier import verify_capture


if __name__=='__main__':
    try:
        validate_database_url(os.environ['SPECIALIST_REPORT_DATABASE_URL'])
        with psycopg.connect(os.environ['SPECIALIST_REPORT_DATABASE_URL'],autocommit=True,connect_timeout=20,row_factory=dict_row) as c:
            c.execute('SET default_transaction_read_only=on')
            result=verify_capture(c,managed_reporting_archive(),operation_id=CAPTURE_OPERATION_ID,manifest_hash=CAPTURE_SOURCE_MANIFEST_SHA256)
        print('SPECIALIST_CAPTURE_READER '+json.dumps(result),flush=True)
    except Exception as error:
        print('SPECIALIST_CAPTURE_READER '+json.dumps({'status':'failed','error_type':type(error).__name__,
              'reason':str(error) if isinstance(error,ValueError) else 'Independent capture verification failed'}),flush=True)
        raise SystemExit(1)
