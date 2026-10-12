"""Reviewed manual producer. Operation/source manifest are deployment-bound constants."""
import json
import os
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_audit_runtime import validate_database_url
from ibkr_etl.specialist_capture import capture_permission_inspection, managed_capture_archive


def main():
    validate_database_url(os.environ['SPECIALIST_AUDIT_DATABASE_URL'])
    with psycopg.connect(os.environ['SPECIALIST_AUDIT_DATABASE_URL'],autocommit=True,connect_timeout=20,
                        row_factory=dict_row) as c:
        result=capture_permission_inspection(c,managed_capture_archive(),operation_id=CAPTURE_OPERATION_ID,
                                            source_manifest_sha256=CAPTURE_SOURCE_MANIFEST_SHA256)
    print('SPECIALIST_AUDIT_CAPTURE '+json.dumps(result),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_AUDIT_CAPTURE '+json.dumps({'status':'failed','operation_id':CAPTURE_OPERATION_ID,
            'error_type':type(error).__name__,'reason':str(error) if isinstance(error,ValueError) else
            'Capture incomplete; inspect retained evidence and recover the same operation; do not create a replacement'}),flush=True)
        raise SystemExit(1)
