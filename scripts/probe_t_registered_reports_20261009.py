"""Read-only tenant-scoped registered report evidence; retain privately for audit."""
import hashlib,json,os
from datetime import datetime,timezone
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings

TENANT='00000000-0000-0000-0000-000000000001'

def main():
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,
        connect_timeout=25,options='-c default_transaction_read_only=on -c statement_timeout=60000') as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
        batch=c.execute("SELECT * FROM onboarding_historical_report_batches WHERE tenant_id=%s AND subject_id='alpha-world-t' ORDER BY requested_at DESC LIMIT 1",(TENANT,)).fetchone()
        if not batch: raise ValueError('No T batch')
        items=list(c.execute('SELECT * FROM onboarding_historical_report_items WHERE tenant_id=%s AND batch_id=%s ORDER BY period_end,report_type',(TENANT,batch['batch_id'])))
        snapshots=list(c.execute("SELECT DISTINCT s.snapshot_id,s.period_end,s.reporting_currency,s.snapshot_payload_json,s.data_sha256,s.status,s.quality_status FROM reporting_snapshots s JOIN onboarding_historical_report_items i ON s.tenant_id=i.tenant_id AND s.snapshot_id=i.snapshot_id WHERE i.tenant_id=%s AND i.batch_id=%s ORDER BY s.period_end",(TENANT,batch['batch_id'])))
        subjects=list(c.execute('SELECT subject_id,lifecycle_status,base_currency,reporting_currency FROM platform_subjects WHERE tenant_id=%s ORDER BY subject_id',(TENANT,)))
        for row in snapshots:
            if hashlib.sha256(row['snapshot_payload_json'].encode()).hexdigest()!=row['data_sha256']: raise ValueError('Snapshot hash mismatch')
        readonly=c.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on'
        if not readonly: raise ValueError('Read-only transaction required')
        result={'captured_at':datetime.now(timezone.utc).isoformat(),'database_read_only':True,'persistent_database_writes':0,'batch':batch,'items':items,'snapshots':snapshots,'subjects':subjects}
        c.rollback()
    raw=json.dumps(result,sort_keys=True,separators=(',',':'),default=str).encode()
    digest=hashlib.sha256(raw).hexdigest()
    blob='review/alpha-world-t/registered-audit-20261009/'+digest+'.json'
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    client=service.get_blob_client('internal-reports',blob)
    client.upload_blob(raw,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
    if client.download_blob().readall()!=raw: raise ValueError('Audit readback mismatch')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'blob':blob,'sha256':digest,'batch_status':batch['status'],'artifacts':len(items),'snapshots':len(snapshots),'database_read_only':True}),flush=True)

if __name__=='__main__': main()
