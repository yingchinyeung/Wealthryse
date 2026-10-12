"""Version 21 private MS inputs and 42 report runs; never releases reports.

The rehearsal executes the identical transaction then rolls it back. Original
payloads, files, approvals, onboarding batches and source tables stay intact.
"""
import copy,hashlib,json,os,sys
from pathlib import Path
from uuid import UUID,uuid5
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.util import canonical_json,utc_now_iso
from ibkr_etl.subject_read_contract import require_ms_hkd_contract
from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id,SNAPSHOT_NAMESPACE
from ibkr_etl.alpha_world_ms_crisis import qualified_snapshot,load,validate,VERSION
from ibkr_etl.alpha_world_ms_analytics import analyze
from ibkr_etl.onboarding_reports import template_evidence
from ibkr_etl.monthly_release import review_package
from generate_month_end_package import _load_frozen_snapshot
from render_onboarding_historical_reports import _upload_review,_immutable_review_blob
TENANT='00000000-0000-0000-0000-000000000001';SUBJECT='alpha-world-ms'
def require(ok,message):
    if not ok:raise ValueError(message)
def sha(data):return hashlib.sha256(data).hexdigest()
def verify_state(before,after,artifacts):
    for key in before:
        if key not in ('snapshots','runs'):require(before[key]==after[key],'Protected state changed: '+key)
    ids={r['snapshot_id'] for r in artifacts};originals={r['original_snapshot_id'] for r in artifacts}
    expected=copy.deepcopy(before['snapshots'])
    for row in expected:
        if row['snapshot_id'] in originals:
            require(row['status']=='frozen','Only private unapproved snapshots may be versioned')
            row['status']='superseded'
    require(expected==[r for r in after['snapshots'] if r['snapshot_id'] not in ids],'Original snapshot changed beyond status')
    added=[r for r in after['snapshots'] if r['snapshot_id'] in ids]
    require(len(added)==21 and all(r['status']=='frozen' and not r['publication_allowed'] for r in added),'Unexpected new snapshots')
    for row in added:
        wanted=next(a for a in artifacts if a['snapshot_id']==row['snapshot_id'])
        require(row['data_sha256']==wanted['data_sha256'] and str(row['period_end'])==wanted['period_end'] and row['reporting_currency']=='HKD','New snapshot binding differs')
    old_runs={r['generation_run_id'] for r in before['runs']}
    require(before['runs']==[r for r in after['runs'] if r['generation_run_id'] in old_runs],'Original runs changed')
    new=[r for r in after['runs'] if r['generation_run_id'] not in old_runs]
    require(len(new)==42 and all(r['snapshot_id'] in ids and r['status']=='awaiting_approval' for r in new),'Unexpected report records')
    for row in new:
        wanted=next(a for a in artifacts if a['snapshot_id']==row['snapshot_id'])
        require(row['output_sha256']==wanted[row['report_type']+'_sha256'],'Registered report hash differs')
def eligible(c,artifacts):
    packages=[]
    for row in artifacts:
        snapshot=c.execute('SELECT * FROM reporting_snapshots WHERE tenant_id=? AND subject_id=? AND snapshot_id=?',(TENANT,SUBJECT,row['snapshot_id'])).fetchone()
        require(active_month_end_snapshot_id(c,tenant_id=TENANT,subject_id=SUBJECT,period_end=row['period_end'])==row['snapshot_id'],'New input is not uniquely active')
        runs=[dict(r) for r in c.execute('SELECT * FROM report_generation_runs WHERE tenant_id=? AND snapshot_id=? ORDER BY generation_run_id',(TENANT,row['snapshot_id']))]
        package=review_package(dict(snapshot),runs,subject_id=SUBJECT,period_end=row['period_end'],container='internal-reports')
        require(package['approved_types']==[] and package['client_distribution']=='prohibited','Approval boundary changed')
        packages.append({'period_end':row['period_end'],'snapshot_id':row['snapshot_id'],'package_sha256':package['package_sha256'],'release_eligible':True,'approved_types':[]})
    return packages
def main():
    b=REGISTRATION_BINDING;mode=b['mode'];require(mode in ('baseline','rehearse','register','verify'),'Unknown phase')
    for name,digest in b['installed_files_sha256'].items():require(sha(Path('/app',name).read_bytes())==digest,'Installed crisis source differs')
    service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def get(blob,digest):
        data=service.get_blob_client('internal-reports',blob).download_blob().readall();require(sha(data)==digest,'Protected accepted bytes differ');return data
    proof=json.loads(get(b['proof_blob'],b['proof_sha256']));artifacts=proof['artifacts']
    require(proof['status']=='passed' and proof['image']==b['image'] and proof['count']==21 and proof['database_writes']==0 and proof['normal_producer_verified'],'Qualified cloud proof required')
    require([r['period_end'] for r in artifacts]==b['periods'],'Reporting coverage differs')
    readonly=mode in ('baseline','verify')
    options='-c statement_timeout=120000 -c lock_timeout=5000'+(' -c default_transaction_read_only=on' if readonly else '')
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=_hybrid_row_factory,connect_timeout=25,options=options) as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL '+('REPEATABLE READ READ ONLY' if readonly else 'SERIALIZABLE'))
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,));c=PostgresConnection(raw)
        require_ms_hkd_contract(c,tenant_id=TENANT)
        if not readonly:raw.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(TENANT+':'+SUBJECT+':private-crisis-report-registration',))
        current=state(c);require(len(current['subjects'])==1 and current['subjects'][0]['lifecycle_status']=='active','Active MS subject required')
        require(current['releases']==[],'Existing MS release needs separate controlled replacement review')
        evidence=template_evidence([r for r in current['templates'] if r['is_current']],subject_id=SUBJECT)
        require(evidence['factsheet']['layout_version']==b['layout_version'],'Factsheet layout differs')
        future=c.execute("SELECT count(*) FROM subject_operating_registrations WHERE tenant_id=? AND subject_id=? AND capability='month_end_reporting' AND operating_state='active' AND effective_to IS NULL",(TENANT,SUBJECT)).fetchone()[0]
        require(future==1,'One active future month-end registration required')
        if mode=='baseline':
            for row in artifacts:
                require(active_month_end_snapshot_id(c,tenant_id=TENANT,subject_id=SUBJECT,period_end=row['period_end'])==row['original_snapshot_id'],'Original input changed')
                old,digest=_load_frozen_snapshot(c,tenant_id=TENANT,account_id=SUBJECT,snapshot_id=row['original_snapshot_id'])
                require(digest==row['original_sha256'],'Original input digest changed')
                saved=next(r for r in current['snapshots'] if r['snapshot_id']==row['original_snapshot_id'])
                require(saved['status']=='frozen','Only unapproved private inputs may be superseded')
            result={'status':'baseline_passed','state':current,'state_sha256':digest_state(current),'template_evidence':evidence,'count':21,'database_writes':0}
            raw.rollback()
        else:
            baseline=json.loads(get(b['baseline_blob'],b['baseline_sha256']));before=baseline['state']
            if mode in ('register','rehearse'):
                require(current==before and digest_state(current)==baseline['state_sha256'],'Registration baseline drifted')
                require(evidence==baseline['template_evidence'],'Approved template evidence changed')
                for row in artifacts:
                    old,digest=_load_frozen_snapshot(c,tenant_id=TENANT,account_id=SUBJECT,snapshot_id=row['original_snapshot_id'])
                    require(digest==row['original_sha256'],'Original frozen payload changed')
                    payload=get(row['payload_blob'],row['data_sha256']);new=json.loads(payload)
                    require(canonical_json(new).encode()==payload,'New input must be canonical')
                    require(new==qualified_snapshot(old,load(c,old,tenant_id=TENANT)),'Accepted stress source or private version changed')
                    require(analyze(old)==analyze(new),'Financial metrics changed')
                    require(str(uuid5(SNAPSHOT_NAMESPACE,f"{TENANT}:{SUBJECT}:{row['period_end']}:{row['data_sha256']}"))==row['snapshot_id'],'Snapshot identity differs')
                    source=c.execute('SELECT source_manifest_json FROM reporting_snapshots WHERE tenant_id=? AND snapshot_id=?',(TENANT,row['original_snapshot_id'])).fetchone()[0]
                    manifest=json.loads(source) if isinstance(source,str) else dict(source)
                    manifest['qualified_crisis_replay']={'version':VERSION,'input_sha256':new['risk']['crisis_replay']['input_sha256'],'market_source_sha256':new['risk']['crisis_replay']['market_source_sha256'],'original_snapshot_id':row['original_snapshot_id'],'original_data_sha256':row['original_sha256']}
                    inserted=c.execute("""INSERT INTO reporting_snapshots(snapshot_id,tenant_id,subject_id,period_start,period_end,as_of_at,reporting_currency,formula_version,source_manifest_json,snapshot_payload_json,data_sha256,quality_status,status,publication_allowed,publication_blockers_json,benchmark_id,benchmark_convention,benchmark_provider,data_as_of,created_at)
                        SELECT ?,tenant_id,subject_id,period_start,period_end,?,reporting_currency,formula_version,?,?,?,'warn','frozen',false,?,benchmark_id,benchmark_convention,benchmark_provider,data_as_of,?
                        FROM reporting_snapshots WHERE tenant_id=? AND snapshot_id=? AND status='frozen' RETURNING snapshot_id""",
                        (row['snapshot_id'],utc_now_iso(),canonical_json(manifest),payload.decode(),row['data_sha256'],canonical_json(new['publication_blockers']),utc_now_iso(),TENANT,row['original_snapshot_id'])).fetchone()
                    require(inserted is not None,'Original private snapshot no longer eligible')
                    changed=c.execute("UPDATE reporting_snapshots SET status='superseded' WHERE tenant_id=? AND snapshot_id=? AND status='frozen'",(TENANT,row['original_snapshot_id']))
                    require(changed.rowcount==1,'Concurrent private input change')
                    for kind,mime in (('factsheet','application/pdf'),('presentation','application/vnd.openxmlformats-officedocument.presentationml.presentation')):
                        content=get(row[kind+'_blob'],row[kind+'_sha256'])
                        name=Path(row[kind+'_blob']).name;dest=_immutable_review_blob(SUBJECT,row['period_end'],Path(name),row[kind+'_sha256'])
                        _upload_review(service.get_blob_client('internal-reports',dest),content,metadata={'sha256':row[kind+'_sha256'],'snapshot_id':row['snapshot_id']},content_type=mime)
                        template_id=SUBJECT+'-completed-factsheet-model' if kind=='factsheet' else 'approved-u16088571-platform-presentation-layout'
                        version='sha256:'+evidence[kind]['sha256']+'+layout:'+(b['layout_version'] if kind=='factsheet' else row['validation']['content_contract_version'])+'+crisis-release:'+b['release_id']
                        c.execute("INSERT INTO report_generation_runs(tenant_id,snapshot_id,report_type,template_id,template_version,status,output_reference,output_sha256,started_at,completed_at) VALUES (?,?,?,?,?,'awaiting_approval',?,?,?,?)",(TENANT,row['snapshot_id'],kind,template_id,version,'internal-reports/'+dest,row[kind+'_sha256'],utc_now_iso(),utc_now_iso()))
                after=state(c);verify_state(before,after,artifacts);packages=eligible(c,artifacts)
                result={'status':'rehearsal_passed' if mode=='rehearse' else 'private_crisis_reports_registered','count':21,'report_count':42,'packages':packages,'source_data_changed':False,'approvals_changed':False,'reports_released':False,'onboarding_changed':False,'prior_report_records_preserved':True,'transaction_rolled_back':mode=='rehearse','database_writes':0 if mode=='rehearse' else 85}
                c.execute("INSERT INTO audit_events(tenant_id,occurred_at,actor_type,actor_id,event_type,subject_id,outcome,details_json) VALUES (?,?,'system','owner-authorized-ms-crisis-evidence','ms_private_crisis_reports_versioned',?,'awaiting_owner_approval',?)",(TENANT,utc_now_iso(),SUBJECT,canonical_json(result)))
                if mode=='register':raw.commit()
                else:
                    raw.rollback();raw.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
                    require(state(c)==before,'Rehearsal rollback changed persistent state');raw.rollback()
            else:
                verify_state(before,current,artifacts);packages=eligible(c,artifacts)
                for row in artifacts:
                    new,digest=_load_frozen_snapshot(c,tenant_id=TENANT,account_id=SUBJECT,snapshot_id=row['snapshot_id'])
                    require(digest==row['data_sha256'],'Installed private input differs');validate(new['risk']['crisis_replay'],as_of=new['data_as_of'],holdings=new['holdings']['holdings'],nav=new['portfolio_summary']['latest_nav']['total_text'])
                    for run in c.execute('SELECT output_reference,output_sha256 FROM report_generation_runs WHERE tenant_id=? AND snapshot_id=?',(TENANT,row['snapshot_id'])):
                        container,name=run['output_reference'].split('/',1);content=service.get_blob_client(container,name).download_blob().readall()
                        require(sha(content)==run['output_sha256'],'Registered download differs')
                result={'status':'private_crisis_reports_verified','count':21,'report_count':42,'packages':packages,'source_data_changed':False,'approvals_changed':False,'reports_released':False,'onboarding_changed':False,'prior_report_records_preserved':True,'database_writes':0};raw.rollback()
    result['future_reporting']={'active_registrations':int(future),'qualified_crisis_producer':True,'owner_approval_required':True,'source_quality_gates_preserved':True}
    data=json.dumps(result,sort_keys=True,indent=2,default=str,allow_nan=False).encode()
    _upload_review(service.get_blob_client('internal-reports',b['result_blob']),data,metadata={'sha256':sha(data)},content_type='application/json')
    print(json.dumps({'status':result['status'],'count':21,'approval_writes':0}),flush=True)
# state helpers are frozen by the local controller from the existing qualified
# baseline capture script. No imports that activate onboarding are performed.
if __name__=='__main__':main()
