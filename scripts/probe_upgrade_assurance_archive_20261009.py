"""Read-only assurance-history preservation before a normal assurance refresh."""
import os,sys,json,hashlib
from datetime import datetime,timezone
from urllib.parse import urlsplit
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
if urlsplit(os.environ['DATABASE_URL']).hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Exact assurance database required')
tenant=os.environ['PORTFOLIO_TENANT_ID']
if tenant!='00000000-0000-0000-0000-000000000001':raise ValueError('Exact tenant required')
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as db:
    db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');db.execute("SET LOCAL TIME ZONE 'UTC'");db.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
    if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
    runs=db.execute('SELECT * FROM portfolio_analyst_assurance_runs ORDER BY assurance_run_id').fetchall()
    findings=db.execute('SELECT * FROM portfolio_analyst_assurance_findings ORDER BY assurance_run_id,check_id').fetchall()
    subjects=[r['subject_id'] for r in db.execute("SELECT subject_id FROM platform_subjects WHERE tenant_id=%s AND lifecycle_status='active' ORDER BY subject_id",(tenant,))]
if set(subjects)!={'U16088571','U16088606','alpha-world-ms','alpha-world-t','hong-kong-china'} or len(subjects)!=5:raise ValueError('Active portfolio scope drift')
package={'scope':'Original assurance rows preserved before normal rerun; no history deletion','runs':runs,'findings':findings,'active_subjects':subjects,'captured_at':datetime.now(timezone.utc).isoformat()}
raw=json.dumps(package,sort_keys=True,separators=(',',':'),default=str).encode();digest=hashlib.sha256(raw).hexdigest()
if len(raw)>10*1024*1024:raise ValueError('Assurance history exceeds bounded archive')
service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
name='review/platform-upgrade-20261009/assurance-before-'+digest+'.json';blob=service.get_blob_client('internal-reports',name)
blob.upload_blob(raw,overwrite=False)
if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=digest:raise ValueError('Exact history readback failed')
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'database_writes':0,'runs_preserved':len(runs),'findings_preserved':len(findings),'active_subjects':subjects,'private_blob':name,'sha256':digest}),flush=True)
