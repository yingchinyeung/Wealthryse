"""Read-only, tenant-scoped supplemental broker and risk evidence."""
import os,sys,json,hashlib
from datetime import datetime,timezone
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
TENANT='00000000-0000-0000-0000-000000000001'
def main():
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=25,options='-c default_transaction_read_only=on -c statement_timeout=180000') as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
        result={}
        for table in ('trades','corporate_actions','cash_transactions','cash_snapshots'):
            result[table]=list(c.execute('SELECT * FROM '+table+" WHERE account_id IN ('U16088571','U16088606')"))
        for table in ('crisis_replay_daily','crisis_replay_components'):
            result[table]=list(c.execute('SELECT * FROM '+table+" WHERE tenant_id=%s AND subject_id IN ('U16088571','U16088606') AND holdings_as_of>='2025-01-20' ORDER BY subject_id,holdings_as_of",(TENANT,)))
        result['crisis_prices']=list(c.execute("SELECT * FROM market_data_prices WHERE tenant_id=%s AND instrument_id LIKE 'crisis-replay-%%' AND price_date BETWEEN '2008-09-05' AND '2009-03-09' ORDER BY symbol,price_date",(TENANT,)))
        result['benchmark_prices']=list(c.execute("SELECT * FROM market_data_prices WHERE tenant_id=%s AND symbol IN ('^GSPC','^HSI','SPY') AND price_date>='2025-01-17' ORDER BY symbol,price_date",(TENANT,)))
        result['subjects']=list(c.execute("SELECT subject_id,lifecycle_status,base_currency,reporting_currency FROM platform_subjects WHERE tenant_id=%s AND subject_id='alpha-world-t'",(TENANT,)))
        result['onboarding_cases']=list(c.execute("SELECT * FROM account_onboarding_cases WHERE tenant_id=%s AND target_subject_id='alpha-world-t'",(TENANT,)))
        result['read_only']=c.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on'
        result['captured_at']=datetime.now(timezone.utc).isoformat();c.rollback()
    raw=json.dumps(result,sort_keys=True,default=str).encode();sha=hashlib.sha256(raw).hexdigest()
    name='review/alpha-world-t/completion-20261009/inputs-'+sha+'.json'
    b=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True)).get_blob_client('internal-reports',name)
    b.upload_blob(raw,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
    if b.download_blob().readall()!=raw:raise ValueError('Evidence readback differs')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'captured','blob':name,'sha256':sha,'read_only':True,'rows':{k:len(v) for k,v in result.items() if isinstance(v,list)}}),flush=True)
if __name__=='__main__':main()
