"""Injected, read-only actual-snapshot MS header qualification on Azure.

The only writes are immutable private PDFs and the verification receipt.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id
from ibkr_etl.monthly_artifacts import render_factsheet,factsheet_layout_version
from ibkr_etl.alpha_world_ms_pdf_validation import validate
from ibkr_etl.alpha_world_ms_analytics import analyze
from ibkr_etl.subject_read_contract import require_ms_hkd_contract
from generate_month_end_package import _load_frozen_snapshot,_require_factsheet_identity

TENANT='00000000-0000-0000-0000-000000000001'
SUBJECT='alpha-world-ms'


def sha(data):return hashlib.sha256(data).hexdigest()


def main():
    b=PROBE_BINDING
    for name,digest in b['installed_files_sha256'].items():
        if sha(Path('/app',name).read_bytes())!=digest:raise ValueError('Installed source binding differs: '+name)
    if factsheet_layout_version(SUBJECT)!=b['layout_version']:raise ValueError('Installed layout differs')
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    artifacts=[]
    def put(name,content,mime):
        client=service.get_blob_client('internal-reports',b['prefix']+name)
        client.upload_blob(content,overwrite=False,content_settings=ContentSettings(content_type=mime),metadata={'sha256':sha(content),'subject_id':SUBJECT})
        if client.download_blob().readall()!=content:raise ValueError('Private output readback differs')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,
            options='-c default_transaction_read_only=on -c statement_timeout=120000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
        if raw.execute('SHOW transaction_read_only').fetchone()[0]!='on':raise ValueError('Read-only required')
        c=PostgresConnection(raw);require_ms_hkd_contract(c,tenant_id=TENANT)
        subject=dict(c.execute('SELECT base_currency,reporting_currency,lifecycle_status FROM platform_subjects WHERE tenant_id=? AND subject_id=?',(TENANT,SUBJECT)).fetchone())
        if subject['base_currency']!='HKD' or subject['reporting_currency']!='HKD':raise ValueError('HKD subject required')
        inventory={
            'batches':[dict(r) for r in c.execute('SELECT batch_id,status,expected_month_count,expected_artifact_count,manifest_sha256 FROM onboarding_historical_report_batches WHERE tenant_id=? AND subject_id=? ORDER BY requested_at DESC',(TENANT,SUBJECT))],
            'factsheet_runs':[dict(r) for r in c.execute("SELECT g.generation_run_id,g.status,g.output_reference,g.output_sha256,g.template_version,s.period_end,s.snapshot_id,s.data_sha256 FROM report_generation_runs g JOIN reporting_snapshots s USING(tenant_id,snapshot_id) WHERE s.tenant_id=? AND s.subject_id=? AND g.report_type='factsheet' ORDER BY g.generation_run_id",(TENANT,SUBJECT))],
            'release_count':c.execute('SELECT count(*) FROM report_artifact_releases r JOIN report_generation_runs g USING(tenant_id,generation_run_id) JOIN reporting_snapshots s USING(tenant_id,snapshot_id) WHERE s.tenant_id=? AND s.subject_id=?',(TENANT,SUBJECT)).fetchone()[0]}
        assets=[dict(r) for r in c.execute("SELECT asset_role,container_name,blob_name,sha256 FROM subject_report_template_assets WHERE tenant_id=? AND subject_id=? AND report_type='factsheet' AND is_current AND approval_status='validated'",(TENANT,SUBJECT))]
        if len(assets)!=2 or {r['asset_role'] for r in assets}!={'template','completed_example'}:raise ValueError('Validated authority pair required')
        with TemporaryDirectory(prefix='ms-headers-read-only-') as directory:
            folder=Path(directory)
            for asset in assets:
                data=service.get_blob_client(asset['container_name'],asset['blob_name']).download_blob().readall()
                if sha(data)!=asset['sha256']:raise ValueError('Authority asset hash differs')
                (folder/(asset['asset_role']+'.pdf')).write_bytes(data)
            for period in b['periods']:
                snapshot_id=active_month_end_snapshot_id(c,tenant_id=TENANT,subject_id=SUBJECT,period_end=period)
                payload,digest=_load_frozen_snapshot(c,tenant_id=TENANT,account_id=SUBJECT,snapshot_id=snapshot_id)
                if payload['reporting_currency']!='HKD':raise ValueError('Actual HKD frozen payload required')
                metrics,_,evidence=analyze(payload)
                path=folder/('AlphaWorld_MS_Factsheet_'+period+'.pdf')
                result=render_factsheet(payload,template_path=folder/'template.pdf',design_model_path=folder/'completed_example.pdf',output_path=path)
                validate(path,payload);_require_factsheet_identity(path,'iStrategy: Alpha World MS',subject_id=SUBJECT)
                data=path.read_bytes();put('outputs/'+path.name,data,'application/pdf')
                artifacts.append({'period_end':period,'snapshot_id':snapshot_id,'data_sha256':digest,
                    'factsheet_sha256':sha(data),'factsheet_blob':b['prefix']+'outputs/'+path.name,
                    'metrics':metrics,'calculation_evidence':evidence,'validation':result['pdf_accuracy_validation']})
                print(json.dumps({'period':period,'status':'pass'}),flush=True)
        raw.rollback()
    proof={'status':'passed','mode':b['mode'],'image':b['image'],'layout_version':b['layout_version'],
        'artifacts':artifacts,'count':len(artifacts),'subject':subject,'report_inventory':inventory,'database_writes':0,
        'read_only_transaction_enforced':True,'source_data_changed':False,'approvals_changed':False,
        'reports_released':False,'onboarding_activated':False,'data_source':'Actual active persistent HKD frozen snapshots'}
    data=json.dumps(proof,sort_keys=True,indent=2,default=str,allow_nan=False).encode()
    put('verification.json',data,'application/json')
    print(json.dumps({'status':'passed','months':len(artifacts),'proof_sha256':sha(data)}),flush=True)


if __name__=='__main__':main()
