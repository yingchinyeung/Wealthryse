"""Replace exactly the rehearsed disposable MS cache, with a protected preimage."""
import os,sys,json,hashlib
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from ibkr_etl.db import PostgresConnection
from ibkr_etl.risk_snapshots import compute_risk_snapshot,decoded_payload,snapshot_read_status
from ibkr_etl.util import canonical_json,utc_now_iso

def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
def validate_transition(old,payload,source,package):
    if canonical_json(old)!=canonical_json(package['old_row']):raise ValueError('Full cache preimage differs')
    if sha(payload)!=package['candidate_payload_sha256'] or source!=package['candidate_source_sha256']:raise ValueError('Qualified candidate differs')
    metrics=decoded_payload(old['payload_json'])['metrics']
    if not metrics or any(payload['metrics'].get(k)!=v for k,v in metrics.items()):raise ValueError('Core metrics changed')

def main():
    from ibkr_etl.qualified_agent_risk import ms_agent_payload
    plan=PLAN
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    content=service.get_blob_client('internal-reports',plan['private_blob']).download_blob().readall()
    if hashlib.sha256(content).hexdigest()!=plan['blob_sha256']:raise ValueError('Recovery blob differs')
    package=json.loads(content);tenant=os.environ['PORTFOLIO_TENANT_ID'];subject='alpha-world-ms';day=package['as_of']
    if tenant!='00000000-0000-0000-0000-000000000001' or package['subject']!=subject or day!='2026-10-07':raise ValueError('Unexpected repair scope')
    scope=(tenant,subject,day,'risk-analytics-v2')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=10,options='-c statement_timeout=120000 -c lock_timeout=5000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
        key=int.from_bytes(hashlib.sha256(canonical_json(['risk-refresh-v1',tenant,subject,day]).encode()).digest()[:8],byteorder='big',signed=True)
        if not raw.execute('SELECT pg_try_advisory_xact_lock(%s) AS acquired',(key,)).fetchone()['acquired']:raise ValueError('Risk worker already holds date lock')
        raw.execute('SELECT 1 FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s FOR UPDATE',scope)
        old=raw.execute('SELECT to_jsonb(r) AS record FROM risk_analytics_snapshots r WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s',scope).fetchone()['record']
        latest=raw.execute("SELECT MAX(snapshot_date) AS day FROM subject_nav_series WHERE tenant_id=%s AND subject_id=%s AND currency='HKD'",(tenant,subject)).fetchone()['day']
        if str(latest)!=day:raise ValueError('Latest source date changed')
        payload,timings,source=compute_risk_snapshot(PostgresConnection(raw),tenant_id=tenant,subject_id=subject,as_of=day)
        validate_transition(old,payload,source,package)
        changed=raw.execute('''UPDATE risk_analytics_snapshots SET payload_json=%s::jsonb,payload_sha256=%s,source_data_sha256=%s,
            generated_at=%s,duration_ms=%s,timings_json=%s::jsonb
            WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s AND payload_sha256=%s''',
            (canonical_json(payload),sha(payload),source,utc_now_iso(),float(timings['total_ms']),canonical_json({**timings,'source_check_status':'current',
            'source_checked_at':utc_now_iso(),'refresh_reason':'independently-qualified-ms-risk-extension'}),*scope,old['payload_sha256'])).rowcount
        if changed!=1:raise ValueError('Expected one cache update')
        verified=ms_agent_payload(raw,tenant_id=tenant,as_of=day,source_snapshot=payload['source_snapshot'],core_metrics=payload['metrics'],crisis=payload['crisis_replay'])
        if sha(verified)!=package['candidate_payload_sha256']:raise ValueError('Agent cache acceptance failed')
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'subject':subject,'as_of':day,'cache_rows_updated':1,
        'core_metrics_identical':True,'source_data_changed':False,'reports_changed':False,'approvals_changed':False,
        'new_payload_sha256':package['candidate_payload_sha256'],'new_source_sha256':package['candidate_source_sha256']}),flush=True)

if __name__=='__main__':main()
