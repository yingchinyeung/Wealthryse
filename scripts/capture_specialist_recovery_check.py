"""Manual read-only inspection with a deliberate first-execution interruption.

No synthetic financial activity and no mutation of retained records. The same
reviewed payload resumes using committed operation state in a separate worker.
Never use this pattern for operations with external side effects.
"""
import json
import os
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_audit_runtime import validate_database_url, bind_audit_session, TENANT
from ibkr_etl.specialist_capture import capture_permission_inspection, managed_capture_archive


class PlannedInterruption(Exception):
    pass


class InterruptBeforeAnchor:
    def __init__(self,archive): self.archive=archive
    def check_ready(self): return self.archive.check_ready()
    def retain_inspection_evidence(self,*args): return self.archive.retain_inspection_evidence(*args)
    def put(self,tenant,event):
        if tenant!=TENANT or event['phase']!='started':
            raise ValueError('Unexpected recovery interruption boundary')
        raise PlannedInterruption('Intent committed; anchor deliberately not written')


def run(c,archive,operation,manifest):
    bind_audit_session(c)
    prior=c.execute("SELECT event_json FROM specialist_audit_events WHERE tenant_id=%s AND event_json->>'operation_id'=%s ORDER BY sequence_no LIMIT 3",
                    (TENANT,operation)).fetchall()
    if len(prior)>2 or (prior and prior[0]['event_json']['phase']!='started'):
        raise ValueError('Unexpected recovery operation state')
    try:
        result=capture_permission_inspection(c,InterruptBeforeAnchor(archive) if not prior else archive,
                    operation_id=operation,source_manifest_sha256=manifest)
    except PlannedInterruption:
        rows=c.execute("SELECT event_json FROM specialist_audit_events WHERE tenant_id=%s AND event_json->>'operation_id'=%s ORDER BY sequence_no LIMIT 3",
                       (TENANT,operation)).fetchall()
        if len(rows)!=1 or rows[0]['event_json']['phase']!='started':
            raise ValueError('Planned stop did not retain exactly one committed intent')
        event=rows[0]['event_json']
        return {'status':'interrupted','operation_id':operation,'expected_interruption':True,
                'database_committed':True,'protected_archive_verified':False,
                'sequence':event['sequence'],'event_sha256':event['sha256'],
                'source_rows_changed':0,'messages_sent':0,'execution_authorized':False}
    if prior and result['phases'][0]['event_sha256']!=prior[0]['event_json']['sha256']:
        raise ValueError('Recovery changed committed intent')
    return {**result,'recovery_rehearsal':True,'resumed_committed_intent':bool(prior)}


def main():
    validate_database_url(os.environ['SPECIALIST_AUDIT_DATABASE_URL'])
    with psycopg.connect(os.environ['SPECIALIST_AUDIT_DATABASE_URL'],autocommit=True,connect_timeout=20,
                        row_factory=dict_row) as c:
        result=run(c,managed_capture_archive(),CAPTURE_OPERATION_ID,CAPTURE_SOURCE_MANIFEST_SHA256)
    print('SPECIALIST_AUDIT_CAPTURE '+json.dumps(result),flush=True)
    return 1 if result['status']=='interrupted' else 0


if __name__=='__main__':
    try: code=main()
    except Exception as error:
        print('SPECIALIST_AUDIT_CAPTURE '+json.dumps({'status':'failed','operation_id':CAPTURE_OPERATION_ID,
            'error_type':type(error).__name__,'reason':'Recovery check incomplete; inspect retained operation before retry'}),flush=True)
        code=1
    raise SystemExit(code)
