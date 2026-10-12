"""Conditional full-preimage restore for the single MS cache row, if needed."""
import os,sys,json,hashlib
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from ibkr_etl.risk_snapshots import snapshot_read_status
from ibkr_etl.util import canonical_json

def main():
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    content=service.get_blob_client('internal-reports',PLAN['private_blob']).download_blob().readall()
    if hashlib.sha256(content).hexdigest()!=PLAN['blob_sha256']:raise ValueError('Protected preimage differs')
    package=json.loads(content);old=package['old_row'];scope=(old['tenant_id'],old['subject_id'],old['as_of_date'],old['analytics_version'])
    if scope!=('00000000-0000-0000-0000-000000000001','alpha-world-ms','2026-10-07','risk-analytics-v2'):raise ValueError('Recovery scope differs')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=10,options='-c statement_timeout=120000 -c lock_timeout=5000') as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        db.execute("SELECT set_config('app.tenant_id',%s,true)",(scope[0],))
        key=int.from_bytes(hashlib.sha256(canonical_json(['risk-refresh-v1',*scope[:3]]).encode()).digest()[:8],byteorder='big',signed=True)
        if not db.execute('SELECT pg_try_advisory_xact_lock(%s) AS acquired',(key,)).fetchone()['acquired']:raise ValueError('Cache busy')
        current=db.execute('SELECT * FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s FOR UPDATE',scope).fetchone()
        if current['payload_sha256']!=package['candidate_payload_sha256'] or current['source_data_sha256']!=package['candidate_source_sha256']:raise ValueError('Current cache differs; manual reconciliation required')
        changed=db.execute('''UPDATE risk_analytics_snapshots SET
            (comparison_version,source_data_sha256,payload_sha256,payload_json,quality_status,generated_at,duration_ms,timings_json)=
            (SELECT comparison_version,source_data_sha256,payload_sha256,payload_json,quality_status,generated_at,duration_ms,timings_json
             FROM jsonb_populate_record(NULL::public.risk_analytics_snapshots,%s::jsonb))
            WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s AND payload_sha256=%s''',
            (canonical_json(old),*scope,package['candidate_payload_sha256'])).rowcount
        restored=db.execute('SELECT to_jsonb(r) AS record FROM risk_analytics_snapshots r WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s',scope).fetchone()['record']
        if changed!=1 or canonical_json(restored)!=canonical_json(old) or snapshot_read_status(restored,subject_id=scope[1],as_of=scope[2])!='current':raise ValueError('Full cache recovery verification failed')
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'cache_rows_restored':1,'full_preimage_restored':True,'source_data_changed':False,'reports_changed':False,'approvals_changed':False}),flush=True)
if __name__=='__main__':main()
