"""Manual, authenticated reporting preflight. No email or schedule activation."""
import json
import os
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_report_runtime import validate_database_url, run_report


def main():
    validate_database_url(os.environ['SPECIALIST_REPORT_DATABASE_URL'])
    # This deployed acceptance payload deliberately exposes no build switch.
    with psycopg.connect(os.environ['SPECIALIST_REPORT_DATABASE_URL'],autocommit=True,connect_timeout=20,
                        row_factory=dict_row) as c:
        result=run_report(c,None,report_date=None,mode='verify')
    print('SPECIALIST_REPORT_WORKER '+json.dumps(dict(result,status='passed')),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_REPORT_WORKER '+json.dumps({'status':'failed','error_type':type(error).__name__,
              'reason':str(error) if isinstance(error,ValueError) else 'Reporting preflight failed; inspect restricted evidence'}),flush=True)
        raise SystemExit(1)
