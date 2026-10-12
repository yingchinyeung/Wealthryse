"""Read-only five-account recovery fingerprints and registered-artifact verification."""
import os,sys,json,hashlib,time
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
SOURCE_HOST='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
TARGET_HOST='ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'
mode=globals().get('RECOVERY_MODE','source')
uri=urlsplit(os.environ['DATABASE_URL'])
if uri.hostname!=SOURCE_HOST or mode not in ('source','restored'):raise ValueError('Unexpected recovery scope')
if mode=='restored':
    userinfo,sep,_=uri.netloc.rpartition('@')
    if not sep or not userinfo:raise ValueError('Existing credential authority required')
    os.environ['DATABASE_URL']=urlunsplit((uri.scheme,userinfo+'@'+TARGET_HOST+(':'+str(uri.port) if uri.port else ''),uri.path,uri.query,uri.fragment))
from ibkr_etl import web
from ibkr_etl.util import canonical_json
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from fastapi.testclient import TestClient

def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
start=time.perf_counter()
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=15) as db:
    db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    db.execute("SELECT set_config('app.tenant_id',%s,true)",(web.TENANT_ID,))
    if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    columns=db.execute("SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()
    names=[r['table_name'] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()]
    tables={}
    for name in names:
        digest=hashlib.sha256();count=0
        with db.cursor(name='upgrade_recovery_rows') as cursor:
            cursor.execute(sql.SQL('SELECT md5(row_to_json(t)::text) AS digest FROM {} t ORDER BY 1').format(sql.Identifier('public',name)))
            for row in cursor:digest.update((row['digest']+'\n').encode());count+=1
        tables[name]={'rows':count,'sha256':digest.hexdigest()}
    subjects=[r['subject_id'] for r in db.execute("SELECT subject_id FROM platform_subjects WHERE tenant_id=%s AND lifecycle_status='active' ORDER BY subject_id",(web.TENANT_ID,)).fetchall()]
    if set(subjects)!={'U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'}:raise ValueError('Five active accounts required')
    artifacts=[dict(r) for r in db.execute("SELECT r.generation_run_id,r.output_reference,r.output_sha256,r.status,s.subject_id,s.period_end,s.status AS snapshot_status FROM report_generation_runs r JOIN reporting_snapshots s ON s.tenant_id=r.tenant_id AND s.snapshot_id=r.snapshot_id WHERE r.tenant_id=%s AND r.output_reference IS NOT NULL AND r.output_sha256 IS NOT NULL AND r.status IN ('released_internal','awaiting_approval') ORDER BY r.generation_run_id",(web.TENANT_ID,)).fetchall()]
service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
verified=[];seen={};mismatches=[]
for artifact in artifacts:
    path=artifact['output_reference']
    # Accept only the established report storage contract, never arbitrary URLs.
    if path.startswith('internal-reports/'):path=path[len('internal-reports/'):]
    elif path.startswith('az://internal-reports/'):path=path[len('az://internal-reports/'):]
    elif path.startswith('azure://internal-reports/'):path=path[len('azure://internal-reports/'):]
    elif path.startswith('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net/internal-reports/'):path=path.split('/internal-reports/',1)[1]
    elif '://' in path or path.startswith('/') or '..' in path.split('/'):raise ValueError('Unexpected report storage path')
    if path not in seen:
        blob=service.get_blob_client('internal-reports',path)
        if blob.get_blob_properties().size>100*1024*1024:raise ValueError('Artifact size exceeds bound')
        seen[path]=hashlib.sha256(blob.download_blob().readall()).hexdigest()
    if seen[path]!=artifact['output_sha256']:
        mismatches.append({**artifact,'actual_sha256':seen[path]})
        continue
    verified.append({'generation_run_id':artifact['generation_run_id'],'sha256':seen[path]})
ready=TestClient(web.app).get('/readyz').status_code
if ready!=200:raise ValueError('Recovery application readiness failed')
result={'passed':not mismatches,'mode':mode,'read_only':True,'database_writes':0,'schema_sha256':sha(columns),'tables':tables,'active_subjects':subjects,'artifact_mismatches':mismatches,
        'registered_artifacts':verified,'distinct_report_blobs_verified':len(seen),'readiness_status':ready,'actual_viewer_sign_in':False,'elapsed_seconds':round(time.perf_counter()-start,3)}
data=canonical_json(result).encode();digest=hashlib.sha256(data).hexdigest();name='review/platform-upgrade-20261009/recovery-'+mode+'-'+digest+'.json'
blob=service.get_blob_client('internal-reports',name);blob.upload_blob(data,overwrite=False)
if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=digest:raise ValueError('Evidence readback differs')
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':not mismatches,'mode':mode,'read_only':True,'database_writes':0,'tables':len(tables),'active_subjects':subjects,'registered_artifacts':len(verified),'artifact_mismatches':len(mismatches),'distinct_blobs':len(seen),'private_blob':name,'sha256':digest,'elapsed_seconds':result['elapsed_seconds']}),flush=True)
