"""Independent read-only verification of the first registered manual producer."""
import hashlib
import json
import uuid
from datetime import datetime

from .specialist_report_runtime import TENANT,bind_report_session,verify_report_access
from .util import canonical_json


def verify_capture(c,archive,*,operation_id,manifest_hash):
    import re
    if str(uuid.UUID(operation_id))!=operation_id or not re.fullmatch('[0-9a-f]{64}',manifest_hash):
        raise ValueError('Registered capture identifiers required')
    bind_report_session(c);verify_report_access(c)
    if archive.check_ready().get('protected_archive_verified') is not True:
        raise ValueError('Protected archive unavailable')
    rows=c.execute("""SELECT sequence_no,event_sha256,event_json FROM specialist_audit_events
        WHERE tenant_id=%s AND event_json->>'operation_id'=%s ORDER BY sequence_no LIMIT 3""",(TENANT,operation_id)).fetchall()
    if len(rows)!=2: raise ValueError('Capture must have exactly two retained phases')
    previous='0'*64
    if rows[0]['sequence_no']>1:
        prior=c.execute('SELECT event_json FROM specialist_audit_events WHERE tenant_id=%s AND sequence_no=%s',
                        (TENANT,rows[0]['sequence_no']-1)).fetchone()
        if not prior: raise ValueError('Capture boundary missing')
        archive.verify(TENANT,prior['event_json']);previous=prior['event_json']['sha256']
    observed=[]
    for offset,(row,phase) in enumerate(zip(rows,('started','succeeded'))):
        event=row['event_json']
        if (row['sequence_no']!=rows[0]['sequence_no']+offset or event.get('sequence')!=row['sequence_no']
            or event.get('sha256')!=row['event_sha256'] or event.get('previous_sha256')!=previous
            or event.get('event_id')!=operation_id+'-'+phase or event.get('operation_id')!=operation_id
            or event.get('phase')!=phase or event.get('role')!='platform_operations'
            or event.get('action')!='inspect_and_analyze'):
            raise ValueError('Capture chain or identity mismatch')
        if hashlib.sha256(canonical_json({k:v for k,v in event.items() if k!='sha256'}).encode()).hexdigest()!=event['sha256']:
            raise ValueError('Capture event hash mismatch')
        archive.verify(TENANT,event)
        evidence_hash=event['evidence_sha256']
        if not re.fullmatch('[0-9a-f]{64}',evidence_hash): raise ValueError('Invalid evidence identifier')
        data=archive._read(f'{TENANT}/evidence/{evidence_hash}.json')
        if hashlib.sha256(data).hexdigest()!=evidence_hash: raise ValueError('Capture detail hash mismatch')
        detail=json.loads(data)
        if (detail.get('schema')!='audit-permission-inspection-v1' or detail.get('operation_id')!=operation_id
            or detail.get('tenant_id')!=TENANT or detail.get('phase')!=phase
            or detail.get('source_manifest_sha256')!=manifest_hash or detail.get('autonomous_specialist') is not False
            or detail.get('producer_kind')!='trusted_manual_rollout_worker'
            or detail.get('role')!='platform_operations' or detail.get('action')!='inspect_and_analyze'
            or detail.get('producer_resource')!='/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/Microsoft.App/jobs/ibkrv2stg-agent-audit'
            or detail.get('producer_principal')!='60313d53-6387-46d4-9bb8-dc5f8ab925a5'
            or detail.get('database_login')!='ibkr_specialist_audit_login'
            or detail.get('financial_or_source_data_included') is not False):
            raise ValueError('Capture detailed evidence scope mismatch')
        result=detail.get('result')
        expected=None if phase=='started' else {'database_access':'verified','reachable_roles_verified':True,
            'source_table_access':False,'source_mutation_authority':False,'ledger_append_only':True,'events_written':0}
        if result!=expected: raise ValueError('Capture detailed result mismatch')
        at=datetime.fromisoformat(event['occurred_at'])
        if at.utcoffset() is None or observed and at<observed[-1]: raise ValueError('Capture timestamps invalid')
        observed.append(at);previous=event['sha256']
    if archive.check_ready().get('protected_archive_verified') is not True: raise ValueError('Archive protection lost')
    return {'status':'passed','operation_id':operation_id,'independent_reader_verified':True,
            'event_count':2,'detail_count':2,'source_rows_changed':0,'messages_sent':0,
            'database_writes':0,'producer_kind':'trusted_manual_rollout_worker'}
