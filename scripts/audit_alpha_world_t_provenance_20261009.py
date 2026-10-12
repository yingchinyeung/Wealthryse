"""Read only the ingestion receipts behind T-dependent source observations."""
import os,sys,json,hashlib
from datetime import datetime,timezone
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory

def main():
    run_ids="SELECT last_seen_run_id FROM nav_snapshots WHERE account_id IN ('U16088571','U16088606') UNION SELECT last_seen_run_id FROM cash_transactions WHERE account_id IN ('U16088571','U16088606') UNION SELECT last_seen_run_id FROM position_snapshots WHERE account_id IN ('U16088571','U16088606')"
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,options='-c statement_timeout=60000',connect_timeout=25) as raw:
        c=PostgresConnection(raw);c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',?,true)",('00000000-0000-0000-0000-000000000001',))
        result={'ingestion_runs':[dict(r) for r in c.execute('SELECT run_id,run_type,started_at,completed_at,status,raw_sha256,raw_path,reconciliation_json FROM ingestion_runs WHERE run_id IN ('+run_ids+') ORDER BY run_id')],
                'raw_reports':[dict(r) for r in c.execute('SELECT * FROM raw_reports WHERE run_id IN ('+run_ids+') ORDER BY run_id,raw_report_id')],
                'captured_at':datetime.now(timezone.utc).isoformat(),'database_read_only':True}
        raw.rollback()
    content=json.dumps(result,sort_keys=True,default=str).encode();digest=hashlib.sha256(content).hexdigest()
    name='review/alpha-world-t/accuracy-audit-20261009/provenance-'+digest+'.json'
    blob=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True)).get_blob_client('internal-reports',name)
    if not blob.exists():blob.upload_blob(content,overwrite=False,metadata={'sha256':digest},content_settings=ContentSettings(content_type='application/json'))
    assert hashlib.sha256(blob.download_blob().readall()).hexdigest()==digest
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'inspected','blob':name,'sha256':digest,'database_read_only':True,'rows':{k:len(v) for k,v in result.items() if isinstance(v,list)}}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','error_class':type(e).__name__}),flush=True)
        raise SystemExit(1)
