"""Qualified image-only promotion, scoped to the approved stress proxy correction."""
import copy,json,sys
from pathlib import Path
from datetime import datetime,timezone
from deploy_metadata_monitor import request,BASE
from deploy_ms_agent_tenant_20261009 import invariant as web_invariant
from deploy_ms_headers_v14r15 import clean
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_t_runtime_release_20261009 import idle,safe_window
from scan_ms_presentation_worker_candidate import verify
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain
import release_extended_crisis_identity_20261009 as candidate
API='?api-version=2024-03-01'

def record(folder):
    result=candidate.release.read(candidate.STAGE/folder/'result.json')
    if result['status']!='Succeeded' or not result['job_settings_preserved'] or len(result['records'])!=1:raise ValueError('Runtime qualification missing')
    value=json.loads(result['records'][0]['Result'])
    if not value['passed']:raise ValueError('Runtime qualification failed')
    return result,value

def gates(kind):
    spec=candidate.configure(kind);folder=candidate.release.WEB
    source,_,output=verify(folder);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=candidate.release.read(folder/'scan-completed.json');remote,_=restore_protected(candidate.release.read(folder/'scan-protected-receipt.json'))
    if scan!=remote or scan['image']!=image or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Protected security gate failed')
    app=candidate.release.read(candidate.STAGE/'app-release-v1/build-completed.json')['output_images'][0]
    app_image=app['registry']+'/'+app['repository']+'@'+app['digest']
    for name in ('history-rehearsal-v2','candidate-canary-v1','history-sql-rehearsal-v1'):
        result,value=record(name)
        if result['image']!=app_image:raise ValueError('Qualification used a different application candidate')
        if name=='history-rehearsal-v2' and (value['qualified']!=446 or value['failed'] or not value['non_crisis_metrics_identical']):raise ValueError('History qualification differs')
        if name=='candidate-canary-v1' and (value['tests_run']!=18 or value['readiness_status']!=200 or not value['exact_tool_dispatch_passed'] or value['database_writes']!=0):raise ValueError('Actual-request acceptance differs')
        if name=='history-sql-rehearsal-v1' and (value['rows_qualified']!=446 or not value['full_preimage_rollback_passed'] or value['persistent_database_writes']!=0):raise ValueError('Rollback acceptance differs')
    recovery=candidate.release.read(candidate.STAGE/'history-rehearsal-v2/protected.json')
    if recovery['blob_sha256']!=value['blob_sha256'] or not recovery['passed']:raise ValueError('Rollback archive differs')
    return spec,image

def read(folder,name):
    value=candidate.release.read(folder/(name+'.json'));remote,_=restore_protected(value['protected_receipt'])
    if remote!={k:v for k,v in value.items() if k!='protected_receipt'}:raise ValueError('Protected deployment evidence differs')
    return value

def save(folder,name,value):
    folder.mkdir(exist_ok=True)
    candidate.release.save(folder/(name+'.json'),{**value,'protected_receipt':retain(value,'crisis-identity-deployment-'+name)})

def patch_for(live,image,*,web=False):
    template=copy.deepcopy(live['properties']['template'])
    if web:template=clean(template)
    if len(template['containers'])!=1:raise ValueError('Single qualified container required')
    for entry in template['containers'][0].get('env',[]):
        if entry.get('secretRef') and entry.get('value')=='':entry.pop('value')
    template['containers'][0]['image']=image
    if web:template['revisionSuffix']='crisis-proxy-1009-v1'
    return {'properties':{'template':template}}

def main(kind,stage):
    spec,image=gates(kind);folder=candidate.STAGE/(kind+'-deployment-v2')
    path=BASE+'Microsoft.App/'+spec['resource'];live=request('GET',path+API)
    inv=web_invariant if kind=='app' else settings_invariant
    actual=live['properties']['template']['containers'][0]['image']
    if any(s.get('value') for s in live['properties']['configuration'].get('secrets',[])):raise ValueError('Inline secrets prohibited')
    auth=request('GET',path+'/authConfigs/current'+API) if kind=='app' else None
    if stage=='plan':
        if folder.exists() or actual!=candidate.release.IMAGE:raise ValueError('Existing plan or changed baseline')
        patch=patch_for(live,image,web=kind=='app');trial=copy.deepcopy(live);trial['properties']['template']=patch['properties']['template']
        if inv(trial)!=inv(live):raise ValueError('Image-only scope failed')
        if kind=='app' and live['properties']['configuration']['activeRevisionsMode']!='Single':raise ValueError('Unexpected revision policy')
        save(folder,'plan',{'before':live,'before_image':actual,'image':image,'invariant':inv(live),'auth':auth,
            'apply_body':patch,'rollback_body':patch_for(live,actual),
            'controller_sha256':candidate.release.sha(Path(__file__)),'authorization':'Owner approved same documented SPY proxy for U16088571 and MS; full scoped upgrade authorized',
            'scope':'Image only; actual NAV, returns, report files, wording, approvals, identity, credentials and schedules unchanged',
            'cache_transition':'Separate atomic 446-row MS disposable risk-cache migration, full preimages protected and temporary-table rollback verified',
            'normal_job_started':False,'full_upgrade_complete':False})
        print('Protected '+kind+' deployment and exact-image rollback plan');return
    plan=read(folder,'plan')
    if plan['controller_sha256']!=candidate.release.sha(Path(__file__)) or inv(live)!=plan['invariant'] or auth!=plan['auth']:raise ValueError('Deployment scope/auth drift')
    if stage in ('apply','restore'):
        safe_window()
        if kind!='app':idle(spec['resource'].split('/')[1])
        else:
            for job in ('ibkrv2stg-risk-snapshots','ibkrv2stg-benchmark-daily','ibkrv2stg-derived-daily'):idle(job)
        expected=plan['before_image'] if stage=='apply' else plan['image']
        if actual!=expected or (folder/(stage+'-intent.json')).exists():raise ValueError('Image drift or existing action; reconcile')
        save(folder,stage+'-intent',{'plan_receipt':plan['protected_receipt'],'started_at':datetime.now(timezone.utc).isoformat()})
        response=request('PATCH',path+API,data=plan['apply_body'] if stage=='apply' else plan['rollback_body'])
        save(folder,stage+'-ack',{'state':response['properties'].get('provisioningState')})
        print(kind+' '+stage+' submitted; no normal job or report generation started');return
    if stage!='verify':raise ValueError('Unknown deployment stage')
    if actual!=image or live['properties']['provisioningState']!='Succeeded':print('Image not yet ready; no acceptance recorded');return
    if kind=='app':
        revision=live['properties']['latestRevisionName']
        if revision!=live['properties']['latestReadyRevisionName'] or not revision.endswith('--crisis-proxy-1009-v1'):raise ValueError('New revision not ready')
        health=request('GET',path+'/revisions/'+revision+API)['properties']
        if health['healthState']!='Healthy' or health.get('trafficWeight')!=100:raise ValueError('Healthy full-traffic revision required')
    save(folder,'verified',{'passed':True,'resource':path,'image':image,'settings_and_auth_preserved':True,'normal_job_started':False,'full_upgrade_complete':False})
    print(kind+' image verified; full upgrade remains incomplete')

if __name__=='__main__':main(*sys.argv[1:3])
