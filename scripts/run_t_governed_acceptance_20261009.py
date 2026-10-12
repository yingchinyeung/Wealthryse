"""Invoke the installed, qualified T acceptance routine; never release or activate.

Retain exact pre/post financial-cache evidence privately. All application writes
belong to one transaction; a failed gate rolls them back. No broker DML or DDL.
"""
import hashlib,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.alpha_world_t_onboarding import refresh_onboarding_analytics
from ibkr_etl.alpha_world_t_analytics import require_contract
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings

TENANT='00000000-0000-0000-0000-000000000001'
CASE='e71119a4-882f-4f87-87ed-024f7ea9fe73'
QUERIES={
 'gate':"SELECT * FROM account_onboarding_checks WHERE tenant_id=%s AND case_id='"+CASE+"' AND check_id='risk_analytics_validated'",
 'risk':"SELECT * FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id='alpha-world-t'",
 'comparisons':"SELECT * FROM performance_comparison_daily WHERE tenant_id=%s AND subject_id='alpha-world-t'",
 'benchmark_master':"SELECT * FROM benchmark_master WHERE tenant_id=%s AND benchmark_id IN ('HSI','GSPC')",
 'benchmark_prices':"SELECT * FROM benchmark_prices WHERE tenant_id=%s AND benchmark_id IN ('HSI','GSPC') AND observation_date>='2025-01-13'",
 'fx':"SELECT * FROM reference_fx_rates WHERE tenant_id=%s AND from_currency IN ('USD','HKD') AND to_currency IN ('USD','HKD') AND rate_date>='2025-01-13'",
}

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()
def sha(value):return hashlib.sha256(canonical(value)).hexdigest()

def main():
    binding=json.loads(os.environ['T_ACCEPTANCE_BINDING'])
    manifest=json.loads(Path('/app/ALPHA_T_RUNTIME_RELEASE.json').read_bytes())
    if manifest['installed_files_sha256']!=binding['installed_files_sha256']:raise ValueError('Release manifest differs')
    for name,digest in binding['installed_files_sha256'].items():
        if hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()!=digest:raise ValueError('Installed qualification source differs')
    if os.environ.get('PORTFOLIO_TENANT_ID')!=TENANT:raise ValueError('Wrong tenant')
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def retain(label,value):
        raw=canonical(value);digest=hashlib.sha256(raw).hexdigest()
        blob='review/alpha-world-t/governed-acceptance-20261009-v1/'+label+'-'+digest+'.json'
        client=service.get_blob_client('internal-reports',blob)
        client.upload_blob(raw,overwrite=False,content_settings=ContentSettings(content_type='application/json'))
        if client.download_blob().readall()!=raw:raise ValueError('Private audit readback failed')
        return {'blob':blob,'sha256':digest}
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
                        options='-c statement_timeout=120000 -c lock_timeout=10000') as raw:
        raw.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,));raw.commit()
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        c=PostgresConnection(raw)
        require_contract(c,tenant_id=TENANT)
        def snapshot():
            return {key:sorted([dict(r) for r in raw.execute(sql,(TENANT,))],key=canonical) for key,sql in QUERIES.items()}
        def protected():
            subjects=[dict(r) for r in raw.execute("SELECT * FROM platform_subjects WHERE tenant_id=%s ORDER BY subject_id",(TENANT,))]
            checks=[dict(r) for r in raw.execute("SELECT * FROM account_onboarding_checks WHERE tenant_id=%s AND NOT(case_id=%s AND check_id='risk_analytics_validated') ORDER BY case_id,check_id",(TENANT,CASE))]
            nav=[dict(r) for r in raw.execute("SELECT subject_id,snapshot_date,currency,total_text FROM subject_nav_series WHERE tenant_id=%s ORDER BY subject_id,snapshot_date,currency",(TENANT,))]
            return {'subjects':sha(subjects),'other_gates':sha(checks),'nav':sha(nav)}
        subject=raw.execute("SELECT lifecycle_status FROM platform_subjects WHERE tenant_id=%s AND subject_id='alpha-world-t'",(TENANT,)).fetchone()
        if subject['lifecycle_status']!='onboarding':raise ValueError('T no longer private onboarding')
        required=raw.execute("SELECT check_id,status FROM account_onboarding_checks WHERE tenant_id=%s AND case_id=%s AND check_id IN ('data_import_reconciled','report_documents_validated','calculation_definition_approved','financial_contract_approved')",(TENANT,CASE)).fetchall()
        if len(required)!=4 or any(r['status']!='pass' for r in required):raise ValueError('Preceding gates incomplete')
        before=snapshot();guard=protected()
        before_receipt=retain('before',{'captured_at':datetime.now(timezone.utc).isoformat(),'rows':before,'guard':guard,'binding':binding})
        evidence=refresh_onboarding_analytics(c,tenant_id=TENANT)
        if not evidence['internal_shareholder_only'] or evidence['onboarding_activated'] or evidence['report_approvals_changed']:raise ValueError('Acceptance scope differs')
        after=snapshot()
        if guard!=protected() or len(after['gate'])!=1 or after['gate'][0]['status']!='pass':raise ValueError('Out-of-scope or incomplete acceptance')
        after_receipt=retain('prepared',{'rows':after,'guard':guard,'acceptance':evidence,'before':before_receipt})
        raw.commit()
        if sha(snapshot())!=sha(after) or protected()!=guard:raise ValueError('Committed readback differs; inspect audit before retrying')
        raw.rollback()
    result={'status':'passed','as_of':evidence['as_of'],'comparison_rows':evidence['benchmark']['comparison_rows'],
        'internal_shareholder_only':True,'risk_gate':'pass','before':before_receipt,'after':after_receipt,
        'nav_unchanged':True,'other_gates_unchanged':True,'lifecycle_unchanged':True,'reports_released':False,'viewer_access_activated':False}
    result['receipt']=retain('completed',result)
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps(result),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'failed','error_class':type(error).__name__,
            'message':str(error) if isinstance(error,(ValueError,AssertionError)) else 'Service details withheld; inspect execution diagnostics.'}),flush=True)
        raise SystemExit(1)
