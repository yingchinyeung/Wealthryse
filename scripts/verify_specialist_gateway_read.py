"""Independent reporting identity verifies a real gateway result's audit chain.

Expected hashes are deployment constants injected by the manual dispatcher.
No portfolio/source-table access, archive repair, publication or email.
"""
import hashlib
import json
import os
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_report_runtime import validate_database_url,bind_report_session,verify_report_access,TENANT
from ibkr_etl.specialist_archive_reader import managed_reporting_archive
from ibkr_etl.specialist_evidence import RegisteredEvidenceReader,inspect_evidence_inventory
from ibkr_etl.agent_observation_evidence import AGENT_PRINCIPAL
from ibkr_etl.util import canonical_json


def main():
    validate_database_url(os.environ['SPECIALIST_REPORT_DATABASE_URL'])
    archive=RegisteredEvidenceReader(managed_reporting_archive())
    with psycopg.connect(os.environ['SPECIALIST_REPORT_DATABASE_URL'],autocommit=True,connect_timeout=15,row_factory=dict_row) as c:
        bind_report_session(c);verify_report_access(c)
        inventory=inspect_evidence_inventory(c,archive)
        rows=c.execute('SELECT event_json FROM specialist_audit_events WHERE tenant_id=%s ORDER BY sequence_no',(TENANT,)).fetchall()
        matched=[];intents={}
        for row in rows:
            e=row['event_json']
            archive.verify(TENANT,e);archive.verify_detail(TENANT,e)
            value=json.loads(archive._archive._read(f"{TENANT}/evidence/{e['evidence_sha256']}.json"))
            if value.get('schema')!='specialist-observation-envelope-v1':continue
            detail=value['observation']
            if detail['phase']=='started':intents[detail['operation_id']]=(e,detail)
            if detail['phase']!='succeeded' or detail['response_sha256']!=EXPECTED_RESPONSE_SHA256:continue
            started=intents.get(detail['operation_id'])
            if started is None:raise ValueError('Verified protected read intent missing')
            if (detail['user_sha256']!=EXPECTED_USER_SHA256 or detail['subject_id']!='U16088571'
                or detail['tool_name']!='get_performance_series' or detail['role']!='data_analyst'
                or detail['agent_sha256']!=hashlib.sha256(AGENT_PRINCIPAL.encode()).hexdigest()
                or detail['request_sha256']!=hashlib.sha256(canonical_json({'subject_id':'U16088571'}).encode()).hexdigest()
                or any(started[1][k]!=detail[k] for k in ('operation_id','user_sha256','agent_sha256','request_sha256','subject_id','tool_name'))
                or started[0]['sequence']>=e['sequence']):
                raise ValueError('Protected account-read result binding differs')
            matched.append(dict(operation_id=detail['operation_id'],intent_sequence=started[0]['sequence'],
                completed_sequence=e['sequence'],completed_event_sha256=e['sha256'],response_sha256=detail['response_sha256']))
        if len(matched)!=1:raise ValueError('Exactly one protected matching account read required')
        archive.check_ready()
    print('SPECIALIST_GATEWAY_READ_VERIFIED '+json.dumps(dict(status='passed',**inventory,**matched[0],
        protected_intent_verified=True,protected_completion_verified=True,result_hash_verified=True,
        source_table_access=False,database_writes=0,messages_sent=0)),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('SPECIALIST_GATEWAY_READ_VERIFIED '+json.dumps(dict(status='failed',error_type=type(error).__name__)),flush=True)
        raise SystemExit(1)
