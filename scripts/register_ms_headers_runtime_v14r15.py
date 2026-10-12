"""Metadata-only registration of 21 qualified factsheets for owner review.

Active subject remains active. Prior approvals, batches, runs and files unchanged.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import UUID

sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.util import canonical_json,utc_now_iso
from ibkr_etl.subject_read_contract import require_ms_hkd_contract
from ibkr_etl.onboarding_reports import template_evidence
from generate_month_end_package import _load_frozen_snapshot
from render_onboarding_historical_reports import _upload_review,_immutable_review_blob
from ibkr_etl import monthly_artifacts as renderer
import generate_month_end_package as month_end
import render_onboarding_historical_reports as historical

TENANT='00000000-0000-0000-0000-000000000001'
SUBJECT='alpha-world-ms'


def require(value,message):
    if not value:raise ValueError(message)


def main():
    b=REGISTRATION_BINDING;mode=b['mode']
    require(mode in ('baseline','register','verify'),'Unknown phase')
    for name,digest in b['installed_files_sha256'].items():
        require(hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()==digest,'Installed layout source drifted')
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    proof_bytes=service.get_blob_client('internal-reports',b['proof_blob']).download_blob().readall()
    require(hashlib.sha256(proof_bytes).hexdigest()==b['proof_sha256'],'Accepted cloud proof differs')
    proof=json.loads(proof_bytes)
    require(proof['status']=='passed' and proof['image']==b['image'] and proof['count']==21 and proof['database_writes']==0,'Qualified read-only cloud outputs required')
    rows=proof['artifacts'];require([r['period_end'] for r in rows]==b['periods'],'Month coverage differs')
    options='-c statement_timeout=120000 -c lock_timeout=5000'+(' -c default_transaction_read_only=on' if mode!='register' else '')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,options=options) as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL '+('SERIALIZABLE' if mode=='register' else 'REPEATABLE READ READ ONLY'))
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,));c=PostgresConnection(raw)
        require_ms_hkd_contract(c,tenant_id=TENANT)
        if mode=='register':raw.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(TENANT+':'+SUBJECT+':header-layout-registration',))
        current=state(c)
        require(len(current['subjects'])==1 and current['subjects'][0]['lifecycle_status']=='active','Existing active MS subject required')
        require(month_end.render_factsheet is renderer.render_factsheet and historical.render_factsheet is renderer.render_factsheet,
                'Normal historical and future renderers must share the qualified implementation')
        require(renderer.factsheet_layout_version(SUBJECT)==b['layout_version'],'Normal renderer layout differs')
        registrations=c.execute("SELECT count(*) FROM subject_operating_registrations WHERE tenant_id=? AND subject_id=? AND capability='month_end_reporting' AND operating_state='active' AND effective_to IS NULL",(TENANT,SUBJECT)).fetchone()[0]
        require(registrations==1,'Exactly one active future month-end reporting registration required')
        evidence=template_evidence([r for r in current['templates'] if r['is_current']],subject_id=SUBJECT)
        require(evidence['factsheet']['layout_version']==b['layout_version'],'Installed template evidence differs')
        for row in rows:
            payload,digest=_load_frozen_snapshot(c,tenant_id=TENANT,account_id=SUBJECT,snapshot_id=row['snapshot_id'])
            require(digest==row['data_sha256'] and payload['reporting_currency']=='HKD','Accepted frozen input drifted')
        if mode=='baseline':
            result={'status':'baseline_passed','state':current,'state_sha256':digest_state(current),'template_evidence':evidence,
                'image':b['image'],'periods':b['periods'],'database_writes':0}
            raw.rollback()
        else:
            original=service.get_blob_client('internal-reports',b['baseline_blob']).download_blob().readall()
            require(hashlib.sha256(original).hexdigest()==b['baseline_sha256'],'Frozen state baseline changed')
            baseline=json.loads(original);before=baseline['state']
            version='sha256:'+evidence['factsheet']['sha256']+'+layout:'+b['layout_version']+'+header-release:'+b['release_id']
            if mode=='register':
                require(current==before and digest_state(current)==baseline['state_sha256'],'Report registration baseline drifted')
                require(evidence==baseline['template_evidence'],'Approved template evidence drifted')
                registered=[]
                for row in rows:
                    content=service.get_blob_client('internal-reports',row['factsheet_blob']).download_blob().readall()
                    require(hashlib.sha256(content).hexdigest()==row['factsheet_sha256'],'Qualified factsheet bytes changed')
                    path=Path(row['factsheet_blob']).name
                    dest=_immutable_review_blob(SUBJECT,row['period_end'],Path(path),row['factsheet_sha256'])
                    _upload_review(service.get_blob_client('internal-reports',dest),content,
                        metadata={'sha256':row['factsheet_sha256'],'snapshot_id':row['snapshot_id']},content_type='application/pdf')
                    run=c.execute("INSERT INTO report_generation_runs(tenant_id,snapshot_id,report_type,template_id,template_version,status,output_reference,output_sha256,started_at,completed_at) VALUES (?,?,'factsheet',?,?,'awaiting_approval',?,?,?,?) RETURNING generation_run_id",
                        (TENANT,row['snapshot_id'],SUBJECT+'-completed-factsheet-model',version,'internal-reports/'+dest,row['factsheet_sha256'],utc_now_iso(),utc_now_iso())).fetchone()[0]
                    registered.append({'period_end':row['period_end'],'generation_run_id':int(run),'sha256':row['factsheet_sha256'],'blob_name':dest})
                after=state(c)
                verify_state(before,after,version)
                result={'status':'factsheets_registered_for_owner_review','image':b['image'],'release_id':b['release_id'],
                    'factsheets':registered,'count':21,'source_data_changed':False,'approvals_changed':False,
                    'prior_report_records_preserved':True,'onboarding_changed':False,'reports_released':False}
                c.execute("INSERT INTO audit_events(tenant_id,occurred_at,actor_type,actor_id,event_type,subject_id,outcome,details_json) VALUES (?,?,'system','owner-authorized-ms-header-release','ms_header_factsheets_regenerated',?,'awaiting_owner_approval',?)",
                    (TENANT,utc_now_iso(),SUBJECT,canonical_json(result)))
                raw.commit()
            else:
                verify_state(before,current,version)
                registered=[r for r in current['runs'] if r['template_version']==version]
                require(len(registered)==21,'Exactly 21 registered factsheets required')
                expected={r['factsheet_sha256'] for r in rows}
                require({r['output_sha256'] for r in registered}==expected and all(r['status']=='awaiting_approval' for r in registered),'Registered output/approval boundary differs')
                for row in registered:
                    container,name=row['output_reference'].split('/',1)
                    content=service.get_blob_client(container,name).download_blob().readall()
                    require(hashlib.sha256(content).hexdigest()==row['output_sha256'],'Registered download readback failed')
                result={'status':'registered_factsheets_verified','count':21,'runs':registered,
                    'source_data_changed':False,'approvals_changed':False,'reports_released':False,
                    'onboarding_changed':False,'prior_report_records_preserved':True,'database_writes':0}
                raw.rollback()
    result['future_reporting']={'status':'passed','active_month_end_registrations':int(registrations),
        'layout_version':b['layout_version'],'shared_historical_and_month_end_renderer':True,
        'normal_entrypoints_invoked':False,'source_quality_and_approval_gates_preserved':True}
    content=json.dumps(result,sort_keys=True,indent=2,default=str,allow_nan=False).encode()
    _upload_review(service.get_blob_client('internal-reports',b['result_blob']),content,
        metadata={'sha256':hashlib.sha256(content).hexdigest()},content_type='application/json')
    print(json.dumps({'status':result['status'],'count':result.get('count',21),'proof_blob':b['result_blob']}),flush=True)


def digest_state(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def verify_state(before,after,version):
    for key in before:
        if key!='runs':require(before[key]==after[key],'Protected original state changed: '+key)
    require(before['runs']==[r for r in after['runs'] if r['template_version']!=version],'Prior report generation records changed')
    new=[r for r in after['runs'] if r['template_version']==version]
    require(len(new)==21 and all(r['report_type']=='factsheet' and r['status']=='awaiting_approval' for r in new),'Unexpected registered report mutation')


if __name__=='__main__':main()
