"""Read-only installed scheduled-identity qualification of actual HKD DB snapshots.

Renders all 21 actual new frozen payloads, not a separately reconstructed review
payload. No database writes, source mutation, onboarding or publication.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.subject_read_contract import require_ms_hkd_contract
from ibkr_etl.alpha_world_ms_hkd import load_risk_snapshot
from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id,_collect_tool_results,assemble_month_end_payload
from ibkr_etl.monthly_artifacts import render_factsheet,factsheet_layout_version
from ibkr_etl.alpha_world_ms_pdf_validation import validate
from generate_month_end_package import _require_factsheet_identity,_load_frozen_snapshot
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings

TENANT='00000000-0000-0000-0000-000000000001'
SUBJECT='alpha-world-ms'
PREFIX='review/alpha-world-ms/hkd-cutover-v14r11-20261007/'


def sha(value):return hashlib.sha256(value).hexdigest()


def main():
    release=json.loads(Path('/app/ALPHA_MS_HKD_RELEASE.json').read_bytes());image=os.environ['ALPHA_MS_IMAGE_DIGEST']
    for name,digest in release['installed_files_sha256'].items():
        if sha(Path('/app',name).read_bytes())!=digest:raise ValueError('Installed consumer source differs')
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    commit=json.loads(service.get_blob_client('internal-reports',PREFIX+'database-cutover-'+image.split(':')[1]+'.json').download_blob().readall())
    if commit['status']!='committed' or commit['image_digest']!=image:raise ValueError('Exact database cutover required')
    periods={r['period_end']:r for r in commit['reports']}
    artifacts=[]
    def put(name,content,kind):
        blob=service.get_blob_client('internal-reports',PREFIX+name)
        blob.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type=kind),metadata={'sha256':sha(content),'subject_id':SUBJECT})
        if blob.download_blob().readall()!=content:raise ValueError('Installed output readback differs')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
            options='-c default_transaction_read_only=on -c statement_timeout=120000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,));c=PostgresConnection(raw)
        require_ms_hkd_contract(c,tenant_id=TENANT)
        subject=dict(c.execute('SELECT base_currency,reporting_currency,lifecycle_status FROM platform_subjects WHERE tenant_id=? AND subject_id=?',(TENANT,SUBJECT)).fetchone())
        if subject!={'base_currency':'HKD','reporting_currency':'HKD','lifecycle_status':'onboarding'}:raise ValueError('Live currency or lifecycle differs')
        assets=[dict(r) for r in c.execute("SELECT asset_role,container_name,blob_name,sha256 FROM subject_report_template_assets WHERE tenant_id=? AND subject_id=? AND report_type='factsheet' AND is_current AND approval_status='validated'",(TENANT,SUBJECT))]
        if len(assets)!=2 or {r['asset_role'] for r in assets}!={'template','completed_example'}:raise ValueError('Approved design authority pair required')
        old_package_cache=c.execute("""SELECT count(*) FROM report_generation_runs r JOIN reporting_snapshots s
            ON s.tenant_id=r.tenant_id AND s.snapshot_id=r.snapshot_id
            WHERE r.tenant_id=? AND s.subject_id=? AND s.reporting_currency<>'HKD'
            AND r.status='awaiting_approval' AND r.report_type='factsheet' AND r.template_version LIKE ?""",
            (TENANT,SUBJECT,'%'+release['layout_version']+'%')).fetchone()[0]
        if old_package_cache:raise ValueError('Legacy USD generated-package cache would suppress the HKD replacement')
        with TemporaryDirectory(prefix='ms-hkd-installed-') as directory:
            folder=Path(directory)
            for asset in assets:
                content=service.get_blob_client(asset['container_name'],asset['blob_name']).download_blob().readall()
                if sha(content)!=asset['sha256']:raise ValueError('Approved design bytes differ')
                (folder/(asset['asset_role']+'.pdf')).write_bytes(content)
            for period,record in sorted(periods.items()):
                snapshot_id=active_month_end_snapshot_id(c,tenant_id=TENANT,subject_id=SUBJECT,period_end=period)
                if snapshot_id!=record['snapshot_id']:raise ValueError('Active month-end snapshot is not the new HKD version')
                payload,digest=_load_frozen_snapshot(c,tenant_id=TENANT,account_id=SUBJECT,snapshot_id=snapshot_id)
                if digest!=record['data_sha256'] or payload['reporting_currency']!='HKD':raise ValueError('Live frozen payload identity differs')
                _,metrics,_=load_risk_snapshot(c,period,tenant_id=TENANT)
                if metrics!=record['metrics'] or payload['risk']['metrics']!=metrics:raise ValueError('Live cache and reporting metrics differ')
                name='AlphaWorld_MS_Factsheet_'+period+'.pdf';target=folder/name
                render_factsheet(payload,template_path=folder/'template.pdf',design_model_path=folder/'completed_example.pdf',output_path=target)
                _require_factsheet_identity(target,'iStrategy: Alpha World MS',subject_id=SUBJECT)
                validate(target,payload)
                content=target.read_bytes();put('installed-outputs/'+name,content,'application/pdf')
                artifacts.append({'period_end':period,'snapshot_id':snapshot_id,'data_sha256':digest,'sha256':sha(content),'blob_name':PREFIX+'installed-outputs/'+name})
                print(json.dumps({'period':period,'installed_database_factsheet':'pass'}),flush=True)
        latest=str(c.execute("SELECT max(snapshot_date) FROM derived_portfolio_nav WHERE tenant_id=? AND portfolio_id=? AND currency='HKD'",(TENANT,SUBJECT)).fetchone()[0])
        _,latest_metrics,_=load_risk_snapshot(c,latest,tenant_id=TENANT)
        if latest_metrics!=commit['latest_metrics']:raise ValueError('Live latest analytics differ')
        # Exercise future collector on actual persistent data. Read-only means
        # it cannot create an approval or compensate for a failed dependency.
        tools=_collect_tool_results(c,tenant_id=TENANT,subject_id=SUBJECT,period_end='2026-09-30',reporting_currency='HKD')
        _,quality,publication,blockers=assemble_month_end_payload(subject_id=SUBJECT,period_start='2025-01-20',period_end='2026-09-30',reporting_currency='HKD',tool_results=tools)
        if quality=='fail' or publication:raise ValueError('Future assembly failed or bypassed publication gate')
        raw.rollback()
    proof={'status':'passed','image_digest':image,'layout_version':factsheet_layout_version(SUBJECT),'subject':subject,
        'artifacts':artifacts,'count':len(artifacts),'latest_date':latest,'latest_metrics':latest_metrics,
        'future_month_end_collector':'passed','publication_blockers':list(blockers),'database_writes':0,
        'legacy_usd_current_layout_package_cache_conflicts':old_package_cache,
        'ibkr_source_changed':False,'reports_released':False,'onboarding_activated':False,
        'data_source':'Actual persistent HKD frozen reporting snapshots and risk caches; no archived review payload substitution'}
    content=json.dumps(proof,sort_keys=True,indent=2,allow_nan=False).encode()
    put('installed-outputs/verification.json',content,'application/json')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'passed','count':len(artifacts),'blob':PREFIX+'installed-outputs/verification.json','sha256':sha(content)}),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','error_class':type(error).__name__,
            'error':str(error) if isinstance(error,(ValueError,TypeError)) else 'Service details redacted'}),flush=True)
        raise SystemExit(1)
