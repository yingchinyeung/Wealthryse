"""Reconcile only the two changing operational tables across the PITR boundary."""
import os,sys,json,hashlib
from datetime import datetime,timezone
from urllib.parse import urlsplit,urlunsplit
import psycopg
from psycopg.rows import dict_row
from psycopg import sql
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl.util import canonical_json
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
point=datetime.fromisoformat('2026-10-09T18:00:19+00:00')
uri=os.environ['DATABASE_URL'];p=urlsplit(uri)
if p.hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Unexpected source')
userinfo,sep,_=p.netloc.rpartition('@')
if not sep:raise ValueError('Existing credentials required')
clone=urlunsplit((p.scheme,userinfo+'@ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'+(':'+str(p.port) if p.port else ''),p.path,p.query,p.fragment))

def records(url):
    with psycopg.connect(url,row_factory=dict_row,connect_timeout=20) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        db.execute("SELECT set_config('app.tenant_id','00000000-0000-0000-0000-000000000001',true)")
        return {table:[r['record'] for r in db.execute(sql.SQL('SELECT to_jsonb(t) AS record FROM {} t').format(sql.Identifier(table))).fetchall()] for table in ('autonomous_agent_runs','autonomy_recovery_state')}

def digest(row):return hashlib.sha256(canonical_json(row).encode()).hexdigest()

def metadata(row):
    return {k:v for k,v in row.items() if k in ('run_id','id','tenant_id','status') or k.endswith('_at') or k.endswith('_time')}

source,restored=records(uri),records(clone);tables={}
for table in source:
    left={digest(r):r for r in source[table]};right={digest(r):r for r in restored[table]}
    current=[metadata(left[h]) for h in left.keys()-right.keys()]
    old=[metadata(right[h]) for h in right.keys()-left.keys()]
    def after(row):
        for key,value in row.items():
            if not (key.endswith('_at') or key.endswith('_time')) or not isinstance(value,str):continue
            try:when=datetime.fromisoformat(value.replace('Z','+00:00'))
            except ValueError:continue
            if when.tzinfo is None:when=when.replace(tzinfo=timezone.utc)
            if when>point:return True
        return False
    tables[table]={'source_rows':len(source[table]),'restored_rows':len(restored[table]),'identical_full_rows':len(left.keys()&right.keys()),'source_only_or_changed_metadata':current,'restored_only_or_changed_metadata':old,'all_current_differences_have_post_restore_timestamps':all(after(r) for r in current),'columns':sorted(source[table][0]) if source[table] else []}
result={'read_only':True,'database_writes':0,'restore_point':point.isoformat(),'tables':tables,'timing_consistent':all(v['all_current_differences_have_post_restore_timestamps'] for v in tables.values()),'zero_data_loss_claimed':False,'production_redirected':False}
raw=canonical_json(result).encode();sha=hashlib.sha256(raw).hexdigest();name='review/platform-upgrade-20261009/recovery-timing-'+sha+'.json'
blob=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True)).get_blob_client('internal-reports',name)
blob.upload_blob(raw,overwrite=False)
if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=sha:raise ValueError('Evidence differs')
print('PLATFORM_CORRECTION_PROBE '+json.dumps({**result,'private_blob':name,'sha256':sha},default=str),flush=True)
