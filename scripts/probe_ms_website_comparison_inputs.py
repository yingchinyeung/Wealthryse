"""Private, repeatable-read MS website inputs; no processing or database writes."""
import hashlib
import json
import os
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

TENANT = '00000000-0000-0000-0000-000000000001'
SUBJECT = 'alpha-world-ms'
PREFIX = 'review/alpha-world-ms/website-comparisons-hkd-20261008-v1/'


def main():
    queries = {
        'subject': "SELECT subject_id,subject_type,base_currency,reporting_currency,lifecycle_status,history_start_date FROM platform_subjects WHERE tenant_id=%s AND subject_id='alpha-world-ms'",
        'mappings': "SELECT benchmark_id,mapping_type,effective_from,effective_to,reporting_currency,created_at FROM portfolio_benchmark_mapping WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY benchmark_id,effective_from",
        'benchmark_master': "SELECT benchmark_id,symbol,name,provider,currency,return_convention,license_status,active FROM benchmark_master WHERE tenant_id=%s AND benchmark_id IN ('GSPC','HSI') ORDER BY benchmark_id",
        'prices': "SELECT benchmark_id,observation_date,level,currency,observation_status,provider,quality_status,raw_sha256,source_reference FROM benchmark_prices WHERE tenant_id=%s AND benchmark_id IN ('GSPC','HSI') AND observation_date>='2025-01-20' AND quality_status IN ('pass','warn') ORDER BY benchmark_id,observation_date,observation_status",
        'fx': "SELECT rate_date,from_currency,to_currency,rate,provider,observation_status,quality_status FROM reference_fx_rates WHERE tenant_id=%s AND ((from_currency='USD' AND to_currency='HKD') OR (from_currency='HKD' AND to_currency='USD')) AND rate_date>='2025-01-13' AND quality_status IN ('pass','warn') ORDER BY rate_date,from_currency,provider",
        'nav': "SELECT snapshot_date,total_nav,currency FROM derived_portfolio_nav WHERE tenant_id=%s AND portfolio_id='alpha-world-ms' AND currency='HKD' ORDER BY snapshot_date",
        'external_flows': "SELECT event_date,SUM(amount_base) amount_base FROM derived_portfolio_cash_ledger WHERE tenant_id=%s AND portfolio_id='alpha-world-ms' AND included=1 AND external_flow=1 AND amount_base IS NOT NULL GROUP BY event_date ORDER BY event_date",
        'comparison_inventory': "SELECT benchmark_id,reporting_currency,calculation_version,quality_status,count(*) row_count,min(observation_date) first_date,max(observation_date) last_date FROM performance_comparison_daily WHERE tenant_id=%s AND subject_id='alpha-world-ms' GROUP BY benchmark_id,reporting_currency,calculation_version,quality_status ORDER BY benchmark_id,reporting_currency,calculation_version,quality_status",
        'contracts': "SELECT definition_version,configuration_json,configuration_sha256,approval_status,effective_from,effective_to FROM subject_calculation_definitions WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND effective_to IS NULL",
        'cases': "SELECT case_id,stage,status,configuration_json FROM account_onboarding_cases WHERE tenant_id=%s AND target_subject_id='alpha-world-ms'",
        'snapshots': "SELECT snapshot_id,period_end,data_sha256,status,reporting_currency FROM reporting_snapshots WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY period_end,created_at",
        'batch': "SELECT batch_id,status,manifest_sha256,template_evidence_sha256,expected_month_count,expected_artifact_count FROM onboarding_historical_report_batches WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY requested_at",
        'report_assets': "SELECT report_type,asset_role,version_label,sha256,byte_count,is_current FROM subject_report_template_assets WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY report_type,asset_role,version_label",
    }
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row, connect_timeout=25,
            options='-c default_transaction_read_only=on -c statement_timeout=120000') as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        if c.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] != 'on':
            raise ValueError('Read-only transaction required')
        result = {key: list(c.execute(sql, (TENANT,)).fetchall()) for key, sql in queries.items()}
        subject = result['subject']
        if len(subject) != 1 or any(subject[0][k] != v for k,v in {
                'base_currency':'HKD','reporting_currency':'HKD','lifecycle_status':'onboarding'}.items()):
            raise ValueError('Unexpected MS currency/lifecycle')
        c.rollback()
    result.update(status='read_only_inputs_captured', captured_at=datetime.now(timezone.utc).isoformat(),
                  database_writes=0, ibkr_source_changed=False, factsheets_changed=False,
                  reports_released=False, onboarding_activated=False)
    content = json.dumps(result, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False).encode()
    digest = hashlib.sha256(content).hexdigest()
    service = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'], credential=DefaultAzureCredential(
        exclude_interactive_browser_credential=True))
    name = PREFIX + 'inputs.json'
    blob = service.get_blob_client('internal-reports', name)
    blob.upload_blob(content, overwrite=False, content_settings=ContentSettings(content_type='application/json'),
                     metadata={'sha256':digest})
    if blob.download_blob().readall() != content:
        raise ValueError('Private input readback differs')
    print('MS_WEBSITE_INPUTS ' + json.dumps({'status':result['status'],'sha256':digest,
        'blob':name,'rows':{key:len(result[key]) for key in queries},'database_writes':0}), flush=True)


if __name__ == '__main__':
    main()
