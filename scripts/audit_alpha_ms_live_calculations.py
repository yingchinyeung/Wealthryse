"""Read-only, repeatable-read MS lineage audit; no financial writes or refetches."""
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

sys.path[:0] = ['/app/src', '/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory

TENANT = '00000000-0000-0000-0000-000000000001'


def main():
    queries = {
        'subjects': "SELECT subject_id,subject_type,base_currency,reporting_currency,lifecycle_status FROM platform_subjects WHERE subject_id IN ('alpha-world-ms','hong-kong-china','U16088571','U16088606')",
        'contracts': "SELECT subject_id,approval_status,definition_version,configuration_json,configuration_sha256,effective_from,effective_to FROM subject_calculation_definitions WHERE subject_id IN ('alpha-world-ms','hong-kong-china') AND effective_to IS NULL",
        'nav': "SELECT * FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-ms' ORDER BY snapshot_date,currency",
        'components': "SELECT * FROM synthetic_portfolio_components WHERE portfolio_id='alpha-world-ms' ORDER BY snapshot_date,component_id",
        'source_nav': "SELECT subject_id,snapshot_date,currency,total_text FROM subject_nav_series WHERE subject_id IN ('U16088571','U16088606','hong-kong-china') AND snapshot_date>='2025-01-20' ORDER BY subject_id,snapshot_date,currency",
        'broker_fx': "SELECT account_id,rate_date,from_currency,to_currency,rate_text FROM fx_rates WHERE rate_date>='2025-01-20' AND from_currency IN ('USD','HKD') AND to_currency IN ('USD','HKD') ORDER BY account_id,rate_date,from_currency,to_currency",
        'market_prices': "SELECT * FROM market_data_prices WHERE instrument_id IN ('hang-seng-price-index','spy-settlement') AND price_date>='2025-01-20' ORDER BY instrument_id,price_date",
        'hkd_risk_free': "SELECT * FROM risk_free_rates WHERE currency='HKD' ORDER BY rate_date,series_id",
        'cash_transactions': "SELECT account_id,effective_date,transaction_type,description,currency,amount_text,row_sha256 FROM cash_transactions WHERE account_id IN ('U16088571','U16088606') AND effective_date>='2025-01-20' ORDER BY account_id,effective_date,source_key",
        'transfers': "SELECT account_id,data_json,row_sha256 FROM flex_rows WHERE account_id IN ('U16088571','U16088606') AND section='Transfers' ORDER BY account_id,row_sha256",
        'hk_nav': "SELECT * FROM derived_portfolio_nav WHERE portfolio_id='hong-kong-china' ORDER BY snapshot_date,currency",
        'hk_ledger': "SELECT * FROM derived_portfolio_cash_ledger WHERE portfolio_id='hong-kong-china' ORDER BY event_date,event_key",
        'hk_cash': "SELECT * FROM derived_portfolio_cash_balances WHERE portfolio_id='hong-kong-china' ORDER BY snapshot_date,currency",
        'hk_positions': "SELECT * FROM derived_portfolio_positions WHERE portfolio_id='hong-kong-china' ORDER BY snapshot_date,conid",
        'mappings': "SELECT m.*,b.symbol,b.name,b.currency AS benchmark_currency,b.return_convention FROM portfolio_benchmark_mapping m JOIN benchmark_master b USING (tenant_id,benchmark_id) WHERE m.subject_id='alpha-world-ms' ORDER BY effective_from",
        'comparisons': "SELECT * FROM performance_comparison_daily WHERE subject_id='alpha-world-ms' ORDER BY observation_date,benchmark_id,calculation_version",
        'risk_cache': "SELECT * FROM risk_analytics_snapshots WHERE subject_id='alpha-world-ms' ORDER BY as_of_date,analytics_version",
    }
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=_hybrid_row_factory,
            connect_timeout=25, options='-c statement_timeout=180000') as raw:
        c = PostgresConnection(raw)
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',?,true)", (TENANT,))
        result = {key: [dict(row) for row in c.execute(sql)] for key,sql in queries.items()}
        result['captured_at'] = datetime.now(timezone.utc).isoformat()
        raw.rollback()
    content = json.dumps(result,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()
    digest = hashlib.sha256(content).hexdigest()
    name = 'review/alpha-world-ms/calculation-audit-20261007/live-' + digest + '.json'
    blob = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(
        exclude_interactive_browser_credential=True)).get_blob_client('internal-reports',name)
    if not blob.exists():
        blob.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type='application/json'),metadata={'sha256':digest})
    if hashlib.sha256(blob.download_blob().readall()).hexdigest() != digest:
        raise ValueError('Private audit readback differs')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'inspected','blob':name,'sha256':digest,
        'rows':{k:len(v) for k,v in result.items() if isinstance(v,list)},'database_read_only':True}),flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','error_class':type(error).__name__,
            'error':str(error) if isinstance(error,(ValueError,KeyError,TypeError)) else 'Service details redacted'}),flush=True)
        raise SystemExit(1)
