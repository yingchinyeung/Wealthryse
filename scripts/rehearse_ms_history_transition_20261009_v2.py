"""Repeatable-read input check and temporary-table full-preimage rollback test."""
import os,sys,json,hashlib
import psycopg
from psycopg.rows import dict_row
sys.path[:0]=['/app/src','/app/scripts']
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from ibkr_etl import web
from ibkr_etl.risk_snapshots import load_risk_inputs,risk_input_fingerprint,decoded_payload
from ibkr_etl.alpha_world_ms_analytics import analyze
from ibkr_etl.util import canonical_json
def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
blob=service.get_blob_client('internal-reports',PLAN['private_blob'])
if blob.get_blob_properties().size>128*1024**2:raise ValueError('Archive too large')
data=blob.download_blob().readall()
if hashlib.sha256(data).hexdigest()!=PLAN['blob_sha256']:raise ValueError('Archived qualification differs')
package=json.loads(data)
if len(package['qualified'])!=445 or len(package['failures'])!=2:raise ValueError('Scope differs')
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=10) as raw:
    # PostgreSQL requires a writable transaction to create even a TEMP table.
    # Commit that session-local definition, then protect every data operation
    # below with a separate READ ONLY transaction. No permanent DDL is issued.
    raw.execute('SET TRANSACTION READ WRITE')
    raw.execute('CREATE TEMP TABLE ms_history_recovery (LIKE public.risk_analytics_snapshots INCLUDING ALL) ON COMMIT PRESERVE ROWS')
    raw.commit()
    raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    raw.execute("SELECT set_config('app.tenant_id',%s,true)",(web.TENANT_ID,))
    if raw.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    connection=web._portable_database_connection(raw)
    for index,item in enumerate(package['qualified']):
        old=item['old_row'];day=item['as_of'];candidate=item['candidate_payload']
        if old['tenant_id']!=web.TENANT_ID or old['subject_id']!='alpha-world-ms' or old['as_of_date']!=day or old['analytics_version']!='risk-analytics-v2':raise ValueError('Foreign history scope')
        current=raw.execute("SELECT to_jsonb(r) AS record FROM risk_analytics_snapshots r WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND as_of_date=%s AND analytics_version='risk-analytics-v2'",(web.TENANT_ID,day)).fetchone()['record']
        if canonical_json(current)!=canonical_json(old):raise ValueError('Historical preimage drift')
        inputs,_=load_risk_inputs(connection,tenant_id=web.TENANT_ID,subject_id='alpha-world-ms',as_of=day)
        if risk_input_fingerprint(inputs)!=item['candidate_source_sha256']:raise ValueError('Qualified source drift')
        if sha(candidate)!=item['candidate_payload_sha256'] or canonical_json(inputs['ms_hkd_snapshot'])!=canonical_json(candidate['source_snapshot']) or canonical_json(inputs['crisis_replay'])!=canonical_json(candidate['crisis_replay']):raise ValueError('Candidate binding differs')
        core,_,_=analyze(inputs['ms_hkd_snapshot'])
        if any(candidate['metrics'].get(k)!=v for k,v in core.items()) or any(candidate['metrics'].get(k)!=v for k,v in decoded_payload(old['payload_json'])['metrics'].items()):raise ValueError('Core metrics differ')
        raw.execute('INSERT INTO ms_history_recovery SELECT * FROM jsonb_populate_record(NULL::public.risk_analytics_snapshots,%s::jsonb)',(canonical_json(old),))
        raw.execute('SAVEPOINT before_extension')
        count=raw.execute('UPDATE ms_history_recovery SET payload_json=%s::jsonb,payload_sha256=%s,source_data_sha256=%s,quality_status=%s WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s',
            (canonical_json(candidate),item['candidate_payload_sha256'],item['candidate_source_sha256'],candidate['quality_status'],web.TENANT_ID,'alpha-world-ms',day,'risk-analytics-v2')).rowcount
        after=raw.execute('SELECT payload_json FROM ms_history_recovery WHERE as_of_date=%s',(day,)).fetchone()['payload_json']
        if count!=1 or sha(after)!=item['candidate_payload_sha256']:raise ValueError('Temporary transition differs')
        raw.execute('ROLLBACK TO SAVEPOINT before_extension');raw.execute('RELEASE SAVEPOINT before_extension')
        restored=raw.execute('SELECT to_jsonb(r) AS record FROM ms_history_recovery r WHERE as_of_date=%s',(day,)).fetchone()['record']
        if canonical_json(restored)!=canonical_json(old):raise ValueError('Full preimage rollback failed')
        if (index+1)%50==0:print('MS_HISTORY_REHEARSAL '+json.dumps({'verified':index+1}),flush=True)
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'read_only':True,'persistent_database_writes':0,'rows_qualified':445,'full_preimage_rollback_passed':True,'fresh_input_fingerprints_match':True,'core_metrics_identical':True,'blob_sha256':PLAN['blob_sha256'],'excluded_insufficient_history':['2025-01-20','2025-01-21']}),flush=True)
