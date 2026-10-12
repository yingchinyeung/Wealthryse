"""Read-only source qualification for the owner-requested HKD/HIBOR change."""
import hashlib
import json
import os
import sys

sys.path[:0] = ['/app/src', '/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory

TENANT = '00000000-0000-0000-0000-000000000001'
PREFIX = 'review/alpha-world-ms/hkd-hibor-20261007/'


def main():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=_hybrid_row_factory,
            connect_timeout=25, options='-c statement_timeout=180000') as raw:
        c = PostgresConnection(raw)
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',?,true)", (TENANT,))
        queries = {
            'subjects': ("SELECT * FROM platform_subjects WHERE subject_id IN ('alpha-world-ms','alpha-world-t','hong-kong-china')", ()),
            'contracts': ("SELECT * FROM subject_calculation_definitions WHERE subject_id IN ('alpha-world-ms','alpha-world-t','hong-kong-china') AND effective_to IS NULL", ()),
            'portfolios': ("SELECT * FROM derived_portfolios WHERE portfolio_id IN ('alpha-world-ms','alpha-world-t','hong-kong-china')", ()),
            'nav': ("SELECT * FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-ms' ORDER BY snapshot_date,currency", ()),
            'components': ("SELECT * FROM synthetic_portfolio_components WHERE portfolio_id='alpha-world-ms' ORDER BY snapshot_date,component_id", ()),
            'broker_fx': ("SELECT account_id,rate_date,from_currency,to_currency,rate_text FROM fx_rates WHERE rate_date>='2025-01-20' AND rate_date<='2026-09-30' AND from_currency IN ('USD','HKD') AND to_currency IN ('USD','HKD') ORDER BY account_id,rate_date,from_currency,to_currency", ()),
            'reference_fx': ("SELECT * FROM reference_fx_rates WHERE rate_date>='2025-01-20' AND rate_date<='2026-09-30' AND from_currency IN ('USD','HKD') AND to_currency IN ('USD','HKD') ORDER BY rate_date,from_currency,to_currency", ()),
            'hkd_risk_free': ("SELECT * FROM risk_free_rates WHERE currency='HKD' ORDER BY rate_date,series_id", ()),
            'frozen_parents': ("SELECT snapshot_id::text,period_end,reporting_currency,data_sha256,status FROM reporting_snapshots WHERE subject_id='alpha-world-ms' ORDER BY period_end", ()),
        }
        result = {key: [dict(row) for row in c.execute(sql, params)] for key,(sql,params) in queries.items()}
        raw.rollback()
    data = json.dumps(result, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False).encode()
    digest = hashlib.sha256(data).hexdigest()
    name = PREFIX + 'source-inspection-' + digest + '.json'
    blob = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'], credential=DefaultAzureCredential(
        exclude_interactive_browser_credential=True)).get_blob_client('internal-reports', name)
    if not blob.exists():
        blob.upload_blob(data, overwrite=False, content_settings=ContentSettings(content_type='application/json'), metadata={'sha256':digest})
    if hashlib.sha256(blob.download_blob().readall()).hexdigest() != digest:
        raise ValueError('Private diagnostic readback differs')
    print('ALPHA_MS_RECOVERY_RESULT ' + json.dumps({'status':'inspected', 'blob':name,
        'sha256':digest,'rows':{k:len(v) for k,v in result.items()},'database_read_only':True}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT ' + json.dumps({'status':'stopped','error_class':type(error).__name__,
            'error':str(error) if isinstance(error,(ValueError,KeyError,TypeError)) else 'Service details redacted'}), flush=True)
        raise SystemExit(1)
