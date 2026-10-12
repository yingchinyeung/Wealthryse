"""Image-only two-worker repair with preserved settings and exact rollback body."""
import copy,json,sys
from pathlib import Path
from datetime import datetime,timezone
import build_ms_presentation_worker_candidate as build
from deploy_metadata_monitor import BASE,request
from deploy_ms_headers_v14r15 import clean
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_t_runtime_release_20261009 import idle,safe_window
from scan_ms_presentation_worker_candidate import verify
from release_t_axis_contract_20261009 import FOLDER,IMAGES

OUT=FOLDER/'cutover';API='?api-version=2024-03-01'
JOBS=('ibkrv2stg-onboard-render','ibkrv2stg-month-end-factsheet')
def read(p):return json.loads(p.read_bytes())
def save(name,value):build.save(OUT,name,value)
def gate():
    source,_,output=verify(FOLDER/'worker')
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=read(FOLDER/'worker/scan-completed.json')
    if scan['image']!=image or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Security gate failed')
    smoke=read(FOLDER/'worker/smoke-v1/runtime-result.json')
    if smoke['status']!='Succeeded' or not smoke['job_settings_unchanged'] or len(smoke['records'])!=1:raise ValueError('Runtime acceptance incomplete')
    evidence=smoke['records'][0]
    if evidence['status']!='passed' or evidence['months_checked']!=21 or evidence['database_connections']:raise ValueError('Runtime acceptance failed')
    return image

def main(stage,name=None):
    if build.az('account','show','--query','id')!=build.SUB:raise ValueError('Wrong subscription')
    if stage=='prepare':
        if OUT.exists():raise ValueError('Cutover plan exists')
        safe_window();image=gate();targets={}
        for jobname in JOBS:
            idle(jobname);path=BASE+'Microsoft.App/jobs/'+jobname
            before=request('GET',path+API)
            if before['properties']['template']['containers'][0]['image']!=IMAGES['worker']:raise ValueError('Worker baseline drift')
            if any(s.get('value') for s in before['properties']['configuration'].get('secrets',[])):raise ValueError('Inline secrets must not be archived')
            template=clean(copy.deepcopy(before['properties']['template']))
            for container in template['containers']:
                for env in container.get('env',[]):
                    if env.get('secretRef') and env.get('value')=='':env.pop('value')
            rollback=copy.deepcopy(template);template['containers'][0]['image']=image
            restored=copy.deepcopy(template);restored['containers'][0]['image']=IMAGES['worker']
            if restored!=rollback:raise ValueError('Rollback template differs from baseline')
            targets[jobname]={'path':path,'before':before,'image':image,'invariant':settings_invariant(before),'apply':{'properties':{'template':template}},'rollback':{'properties':{'template':rollback}}}
        web=request('GET',BASE+'Microsoft.App/containerApps/ibkrv2stg-web'+API)
        if web['properties']['template']['containers'][0]['image']!=IMAGES['web']:raise ValueError('Website image drift')
        OUT.mkdir();save('plan',{'created_at':datetime.now(timezone.utc).isoformat(),'targets':targets,'website_image':IMAGES['web'],'scope':'One-file T performance-axis validation handoff; no normal rendering, financial data, approval or access writes','rollback_body_verified':True})
        print(json.dumps({'prepared':True,'targets':list(targets),'website_changed':False}));return
    plan=read(OUT/'plan.json')
    if name not in JOBS or stage not in ('apply','verify','rollback'):raise ValueError('Unknown operation')
    target=plan['targets'][name];current=request('GET',target['path']+API)
    if settings_invariant(current)!=target['invariant']:raise ValueError('Non-image settings drift')
    actual=current['properties']['template']['containers'][0]['image']
    if stage in ('apply','rollback'):
        safe_window();idle(name)
        if stage=='apply' and gate()!=target['image']:raise ValueError('Qualified image changed')
        expected=IMAGES['worker'] if stage=='apply' else target['image']
        if actual!=expected:raise ValueError('Unexpected pre-mutation image; reconcile')
        save(name+'-'+stage+'-intent',{'from_image':actual,'to_image':target[stage]['properties']['template']['containers'][0]['image'],'plan_sha256':build.sha(OUT/'plan.json')})
        ack=request('PATCH',target['path']+API,data=target[stage]);save(name+'-'+stage+'-ack',{'id':ack.get('id'),'state':ack.get('properties',{}).get('provisioningState')})
        print(json.dumps({'submitted':name,'image_only':True}));return
    if actual!=target['image']:raise ValueError('Installed image mismatch')
    if current['properties']['provisioningState']!='Succeeded':print(json.dumps({'waiting':name}));return
    save(name+'-verified',{'status':'passed','image':actual,'settings_preserved':True,'source_and_approval_writes':0})
    print(json.dumps({'verified':name,'settings_preserved':True}))

if __name__=='__main__':main(*sys.argv[1:])
