"""Export private T qualification inputs from one read-only database snapshot.

No migrations, source refresh, financial writes, approvals or activation.
An existing reporting execution identity writes only a content-addressed audit.
"""
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
        'subjects': "SELECT subject_id,subject_type,base_currency,reporting_currency,lifecycle_status,history_start_date FROM platform_subjects WHERE subject_id IN ('alpha-world-t','alpha-world-ms','U16088571','U16088606')",
        'contracts': "SELECT subject_id,approval_status,definition_version,configuration_json,configuration_sha256,effective_from,effective_to FROM subject_calculation_definitions WHERE subject_id IN ('alpha-world-t','alpha-world-ms') AND effective_to IS NULL",
        'accounts': "SELECT account_id,alias,base_currency FROM accounts WHERE account_id IN ('U16088571','U16088606')",
        'source_nav': "SELECT subject_id,snapshot_date,currency,total_text FROM subject_nav_series WHERE subject_id IN ('U16088571','U16088606') AND snapshot_date>='2025-01-20' ORDER BY subject_id,snapshot_date,currency",
        'broker_fx': "SELECT account_id,rate_date,from_currency,to_currency,rate_text FROM fx_rates WHERE rate_date>='2025-01-20' AND from_currency IN ('USD','HKD') AND to_currency IN ('USD','HKD') ORDER BY account_id,rate_date,from_currency,to_currency",
        'market_prices': "SELECT * FROM market_data_prices WHERE instrument_id IN ('hang-seng-price-index','spy-settlement') AND price_date>='2025-01-17' ORDER BY instrument_id,price_date",
        'hkd_risk_free': "SELECT * FROM risk_free_rates WHERE currency='HKD' ORDER BY rate_date,series_id",
        'existing_t_nav': "SELECT * FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-t' ORDER BY snapshot_date,currency",
        'existing_t_components': "SELECT * FROM synthetic_portfolio_components WHERE portfolio_id='alpha-world-t' ORDER BY snapshot_date,component_id",
        'mappings': "SELECT m.*,b.symbol,b.name,b.currency AS benchmark_currency,b.return_convention FROM portfolio_benchmark_mapping m JOIN benchmark_master b USING (tenant_id,benchmark_id) WHERE m.subject_id='alpha-world-t' ORDER BY effective_from",
    }
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=_hybrid_row_factory,
            connect_timeout=25, options='-c statement_timeout=180000') as raw:
        c = PostgresConnection(raw)
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',?,true)", (TENANT,))
        result = {key: [dict(row) for row in c.execute(sql)] for key,sql in queries.items()}
        result['captured_at'] = datetime.now(timezone.utc).isoformat()
        result['database_read_only'] = True
        raw.rollback()
    content = json.dumps(result,sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()
    digest = hashlib.sha256(content).hexdigest()
    name = 'review/alpha-world-t/onboarding-20261008/live-' + digest + '.json'
    blob = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(
        exclude_interactive_browser_credential=True)).get_blob_client('internal-reports',name)
    if not blob.exists():
        blob.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type='application/json'),metadata={'sha256':digest})
    if hashlib.sha256(blob.download_blob().readall()).hexdigest() != digest:
        raise ValueError('Private audit readback differs')
    # Existing one-shot controller reads this legacy transport prefix only.
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'subject_id':'alpha-world-t',
        'status':'inspected','blob':name,'sha256':digest,
        'rows':{k:len(v) for k,v in result.items() if isinstance(v,list)},'database_read_only':True}),flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','error_class':type(error).__name__,
            'error':str(error) if isinstance(error,(ValueError,KeyError,TypeError)) else 'Service details redacted'}),flush=True)
        raise SystemExit(1)
