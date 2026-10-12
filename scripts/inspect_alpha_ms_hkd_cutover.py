"""Read-only live cutover preflight; never prints credentials or source payloads."""
import hashlib
import json
import os
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings

TENANT = '00000000-0000-0000-0000-000000000001'
PREFIX = 'review/alpha-world-ms/hkd-cutover-v14r11-20261007/'


def main():
    queries = {
        'role': "SELECT current_user, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname=current_user",
        'subjects': "SELECT subject_id,subject_type,base_currency,reporting_currency,lifecycle_status,history_start_date FROM platform_subjects WHERE tenant_id=%s AND subject_id IN ('alpha-world-ms','alpha-world-t')",
        'contracts': "SELECT definition_id::text,subject_id,definition_version,configuration_json,configuration_sha256,approval_status,effective_from,effective_to,approved_by_identity_sha256 FROM subject_calculation_definitions WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND effective_to IS NULL",
        'nav_coverage': "SELECT currency,count(*) AS count,min(snapshot_date) AS first_date,max(snapshot_date) AS last_date FROM derived_portfolio_nav WHERE tenant_id=%s AND portfolio_id='alpha-world-ms' GROUP BY currency ORDER BY currency",
        'hibor_coverage': "SELECT series_id,count(*) AS count,min(rate_date) AS first_date,max(rate_date) AS last_date FROM risk_free_rates WHERE tenant_id=%s AND currency='HKD' GROUP BY series_id",
        'table_permissions': "SELECT table_name,has_table_privilege(current_user,'public.'||table_name,'SELECT') AS can_read,has_table_privilege(current_user,'public.'||table_name,'INSERT') AS can_insert,has_table_privilege(current_user,'public.'||table_name,'UPDATE') AS can_update,has_table_privilege(current_user,'public.'||table_name,'DELETE') AS can_delete FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('platform_subjects','subject_calculation_definitions','derived_portfolios','derived_portfolio_nav','synthetic_portfolio_components','risk_free_rates','risk_analytics_snapshots') ORDER BY table_name",
        'temp_permission': "SELECT has_database_privilege(current_user,current_database(),'TEMP') AS can_create_temporary_tables",
    }
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row, connect_timeout=25,
            options='-c default_transaction_read_only=on -c statement_timeout=120000') as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        result = {name:list(c.execute(sql,(TENANT,) if '%s' in sql else ()).fetchall())
            for name,sql in queries.items()}
        result['captured_at'] = datetime.now(timezone.utc).isoformat()
        result['database_writes'] = 0
        result['ibkr_source_changed'] = False
        c.rollback()
    raw = json.dumps(result,sort_keys=True,separators=(',',':'),default=str).encode()
    digest = hashlib.sha256(raw).hexdigest()
    blob_name = PREFIX+'preflight-'+digest+'.json'
    blob = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(
        exclude_interactive_browser_credential=True)).get_blob_client('internal-reports',blob_name)
    blob.upload_blob(raw,overwrite=False,content_settings=ContentSettings(content_type='application/json'),
        metadata={'sha256':digest})
    if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=digest:
        raise ValueError('Private preflight readback differs')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'inspected','blob':blob_name,
        'sha256':digest,'database_writes':0}),flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped',
            'error_class':type(error).__name__,'error':'Service details redacted'}),flush=True)
        raise SystemExit(1)
