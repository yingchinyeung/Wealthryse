"""Read-only T and dependent-source audit; never refresh, migrate or approve."""
import hashlib,json,os,sys
from datetime import datetime,timezone
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory

TENANT='00000000-0000-0000-0000-000000000001'
ACCOUNTS="('U16088571','U16088606')"

def main():
    queries={
      'subjects':"SELECT subject_id,subject_type,base_currency,reporting_currency,lifecycle_status,history_start_date FROM platform_subjects WHERE subject_id IN ('alpha-world-t','alpha-world-ms','U16088571','U16088606')",
      'contracts':"SELECT subject_id,approval_status,definition_version,configuration_json,configuration_sha256,effective_from,effective_to FROM subject_calculation_definitions WHERE subject_id IN ('alpha-world-t','alpha-world-ms') AND effective_to IS NULL",
      'accounts':f"SELECT account_id,alias,base_currency FROM accounts WHERE account_id IN {ACCOUNTS}",
      'source_nav':f"SELECT subject_id,snapshot_date,currency,total_text FROM subject_nav_series WHERE subject_id IN {ACCOUNTS} AND snapshot_date>='2025-01-20' ORDER BY subject_id,snapshot_date,currency",
      'nav_details':f"SELECT * FROM nav_snapshots WHERE account_id IN {ACCOUNTS} AND snapshot_date>='2025-01-20' ORDER BY account_id,snapshot_date,currency,asset_class",
      'cash_details':f"SELECT * FROM cash_snapshots WHERE account_id IN {ACCOUNTS} AND snapshot_date>='2025-01-20' ORDER BY account_id,snapshot_date,currency",
      'position_details':f"SELECT * FROM position_snapshots WHERE account_id IN {ACCOUNTS} AND snapshot_date>='2025-01-20' ORDER BY account_id,snapshot_date,conid,model_code",
      'cash_transactions':f"SELECT * FROM cash_transactions WHERE account_id IN {ACCOUNTS} AND effective_date>='2025-01-20' ORDER BY account_id,effective_date,source_key",
      'broker_fx':f"SELECT * FROM fx_rates WHERE account_id IN {ACCOUNTS} AND rate_date>='2025-01-20' ORDER BY account_id,rate_date,from_currency,to_currency",
      'market_prices':"SELECT * FROM market_data_prices WHERE instrument_id IN ('hang-seng-price-index','spy-settlement') AND price_date>='2025-01-17' ORDER BY instrument_id,price_date",
      'hkd_risk_free':"SELECT * FROM risk_free_rates WHERE currency='HKD' ORDER BY rate_date,series_id",
      'existing_t_nav':"SELECT * FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-t' ORDER BY snapshot_date,currency",
      'existing_t_components':"SELECT * FROM synthetic_portfolio_components WHERE portfolio_id='alpha-world-t' ORDER BY snapshot_date,component_id",
      'mappings':"SELECT m.*,b.symbol,b.name,b.currency AS benchmark_currency,b.return_convention FROM portfolio_benchmark_mapping m JOIN benchmark_master b USING(tenant_id,benchmark_id) WHERE m.subject_id='alpha-world-t' ORDER BY effective_from",
      'reporting_snapshots':"SELECT * FROM reporting_snapshots WHERE subject_id='alpha-world-t' ORDER BY period_end",
      'report_generation_runs':"SELECT g.* FROM report_generation_runs g JOIN reporting_snapshots s USING(snapshot_id) WHERE s.subject_id='alpha-world-t' ORDER BY g.generation_run_id",
      'schema':"SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema='public' AND (table_name LIKE '%onboarding%' OR table_name LIKE '%report%' OR table_name LIKE '%risk%' OR table_name LIKE '%source%') ORDER BY table_name,ordinal_position",
    }
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
                         options='-c statement_timeout=180000') as raw:
        c=PostgresConnection(raw)
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',?,true)",(TENANT,))
        result={key:[dict(r) for r in c.execute(sql)] for key,sql in queries.items()}
        present={r['table_name'] for r in result['schema']}
        for table in ('account_onboarding_cases','subject_report_templates','risk_analytics_snapshots',
                      'onboarding_historical_report_batches','subject_operating_registrations'):
            if table in present and any(r['table_name']==table and r['column_name']=='subject_id' for r in result['schema']):
                result[table]=[dict(r) for r in c.execute("SELECT * FROM "+table+" WHERE subject_id='alpha-world-t'")]
        result['captured_at']=datetime.now(timezone.utc).isoformat()
        result['database_read_only']=True
        result['transaction_read_only']=c.execute('SHOW transaction_read_only').fetchone()[0]
        raw.rollback()
    content=json.dumps(result,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()
    digest=hashlib.sha256(content).hexdigest();name='review/alpha-world-t/accuracy-audit-20261009/live-'+digest+'.json'
    blob=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(
        exclude_interactive_browser_credential=True)).get_blob_client('internal-reports',name)
    if not blob.exists():blob.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type='application/json'),metadata={'sha256':digest})
    if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=digest:raise ValueError('Audit readback hash differs')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'inspected','blob':name,'sha256':digest,
        'captured_at':result['captured_at'],'database_read_only':True,'transaction_read_only':result['transaction_read_only'],
        'rows':{k:len(v) for k,v in result.items() if isinstance(v,list)}}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','error_class':type(error).__name__,
            'error':str(error) if isinstance(error,(ValueError,KeyError,TypeError)) else 'Service details redacted'}),flush=True)
        raise SystemExit(1)
