"""Rehearse in a temporary table; persist only a private rollback evidence blob."""
import os,sys,json,hashlib
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from ibkr_etl.db import PostgresConnection
from ibkr_etl.risk_snapshots import compute_risk_snapshot,decoded_payload,snapshot_read_status
from ibkr_etl.util import canonical_json

def sha(v):return hashlib.sha256(canonical_json(v).encode()).hexdigest()

def main():
    tenant=os.environ['PORTFOLIO_TENANT_ID'];day=EXPECTED['as_of'][:10];subject='alpha-world-ms'
    if tenant!='00000000-0000-0000-0000-000000000001':raise ValueError('Wrong tenant')
    scope=(tenant,subject,day,'risk-analytics-v2')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=10,options='-c statement_timeout=120000 -c lock_timeout=5000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
        old=raw.execute('SELECT to_jsonb(r) AS record FROM risk_analytics_snapshots r WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s',scope).fetchone()['record']
        if old['payload_sha256']!=EXPECTED['existing_payload_sha256'] or old['source_data_sha256']!=EXPECTED['existing_source_sha256']:raise ValueError('Qualified cache drifted')
        payload,timings,source=compute_risk_snapshot(PostgresConnection(raw),tenant_id=tenant,subject_id=subject,as_of=day)
        if sha(payload)!=EXPECTED['candidate_payload_sha256'] or source!=EXPECTED['candidate_source_sha256']:raise ValueError('Qualified candidate drifted')
        prior=decoded_payload(old['payload_json'])
        if any(payload['metrics'].get(k)!=v for k,v in prior['metrics'].items()):raise ValueError('Original core changed')
        raw.execute('CREATE TEMP TABLE ms_upgrade_rehearsal (LIKE public.risk_analytics_snapshots INCLUDING ALL) ON COMMIT DROP')
        raw.execute('INSERT INTO ms_upgrade_rehearsal SELECT * FROM jsonb_populate_record(NULL::public.risk_analytics_snapshots,%s::jsonb)',(canonical_json(old),))
        raw.execute('SAVEPOINT before_extension')
        changed=raw.execute('UPDATE ms_upgrade_rehearsal SET payload_json=%s::jsonb,payload_sha256=%s,source_data_sha256=%s WHERE payload_sha256=%s',
            (canonical_json(payload),sha(payload),source,old['payload_sha256'])).rowcount
        if changed!=1:raise ValueError('Temporary cache update failed')
        row=raw.execute('SELECT * FROM ms_upgrade_rehearsal').fetchone()
        if snapshot_read_status(row,subject_id=subject,as_of=day)!='current':raise ValueError('Temporary cache validation failed')
        raw.execute('ROLLBACK TO SAVEPOINT before_extension')
        restored=raw.execute('SELECT to_jsonb(r) AS record FROM ms_upgrade_rehearsal r').fetchone()['record']
        if canonical_json(restored)!=canonical_json(old):raise ValueError('Temporary rollback differs')
        raw.rollback()
    package={'subject':subject,'as_of':day,'old_row':old,'candidate_payload':payload,'candidate_source_sha256':source,
        'candidate_payload_sha256':sha(payload),'timings':timings,'core_metrics_identical':True,
        'temporary_table_update_and_rollback_passed':True,'persistent_database_writes':0}
    data=canonical_json(package).encode();digest=hashlib.sha256(data).hexdigest()
    name='review/platform-upgrade-20261009/ms-cache-rehearsal-'+digest+'.json'
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    blob=service.get_blob_client('internal-reports',name)
    blob.upload_blob(data,overwrite=False)
    if blob.download_blob().readall()!=data:raise ValueError('Rollback archive readback differs')
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'subject':subject,'as_of':day,'persistent_database_writes':0,
        'temporary_table_update_and_rollback_passed':True,'core_metrics_identical':True,'private_blob':name,'blob_sha256':digest,
        'candidate_payload_sha256':sha(payload),'candidate_source_sha256':source,'old_payload_sha256':old['payload_sha256']}),flush=True)

if __name__=='__main__':main()
