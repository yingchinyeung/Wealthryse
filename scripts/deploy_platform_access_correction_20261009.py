"""Image-only website cutover; preserve authentication, settings and all jobs."""
import copy,json,sys
from pathlib import Path
from deploy_metadata_monitor import request,BASE
from deploy_ms_headers_v14r15 import clean,web_hash
from deploy_t_runtime_release_20261009 import safe_window
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected
from scan_ms_presentation_worker_candidate import verify
import release_platform_access_correction_20261009 as release

FOLDER=release.FOLDER/'access-deployment-v1'
WEB=BASE+'Microsoft.App/containerApps/ibkrv2stg-web';API='?api-version=2024-03-01'
REVISION='platform-access-1009-v2'

def invariant(value):
    # ARM includes unset optional fields as null in GET responses, while the
    # PATCH body omits them. Normalize only this representational difference.
    return web_hash(clean(value))
def save(name,value):
    release.save(FOLDER/(name+'.json'),{**value,'protected_receipt':retain(value,'platform-access-'+name)})
def load(name):
    record=release.read(FOLDER/(name+'.json'));remote,_=restore_protected(record['protected_receipt'])
    if remote!={k:v for k,v in record.items() if k!='protected_receipt'}:raise ValueError('Protected evidence mismatch')
    return record
def gate():
    _,_,output=verify(release.WEB);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=release.read(release.WEB/'scan-completed.json');remote,_=restore_protected(release.read(release.WEB/'scan-protected-receipt.json'))
    if scan!=remote or scan['image']!=image or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Security gate failed')
    return image
def main(stage):
    image=gate();current=request('GET',WEB+API);auth=request('GET',WEB+'/authConfigs/current'+API)
    actual=current['properties']['template']['containers'][0]['image']
    if stage=='plan':
        if FOLDER.exists() or actual!=release.IMAGE:raise ValueError('Existing plan or baseline drift')
        if current['properties']['configuration']['activeRevisionsMode']!='Single':raise ValueError('Unexpected revision mode')
        if any(secret.get('value') for secret in current['properties']['configuration'].get('secrets',[])):raise ValueError('Do not archive inline credentials')
        template=clean(copy.deepcopy(current['properties']['template']))
        for container in template['containers']:
            for env in container.get('env',[]):
                if env.get('secretRef') and env.get('value')=='':env.pop('value')
        rollback=copy.deepcopy(template)
        template['containers'][0]['image']=image;template['revisionSuffix']=REVISION
        simulated=copy.deepcopy(current);simulated['properties']['template']=template
        if invariant(simulated)!=invariant(current):raise ValueError('Non-image settings differ')
        restored=copy.deepcopy(simulated);restored['properties']['template']=rollback
        if invariant(restored)!=invariant(current) or rollback['containers'][0]['image']!=release.IMAGE:raise ValueError('Rollback roundtrip failed')
        prior=release.read(release.PARENT.parent/'cutover/verified.json')
        remote,_=restore_protected(prior['protected_receipt'])
        if remote!={k:v for k,v in prior.items() if k!='protected_receipt'} or prior['image']!=release.IMAGE or prior['status']!='passed':raise ValueError('Qualified fallback missing')
        FOLDER.mkdir()
        save('plan',{'before':current,'auth':auth,'invariant':invariant(current),'image':image,
          'apply_body':{'properties':{'template':template}},'rollback_body':{'properties':{'template':rollback}},
          'rollback_roundtrip_verified':True,'fallback_previously_qualified':True,
          'authorization':'User requested all recommended corrections; Grok confirmed stopped before continuation',
          'database_writes':0,'jobs_changed':False,'report_bytes_changed':False})
        print('Scoped deployment plan and rollback verified');return
    plan=load('plan')
    if invariant(current)!=plan['invariant'] or auth['properties']!=plan['auth']['properties'] or image!=plan['image']:raise ValueError('Configuration or authentication drift')
    if stage=='apply':
        safe_window()
        if actual!=release.IMAGE:raise ValueError('Unexpected image; reconcile unknown outcome')
        save('apply-intent',{'from_image':actual,'to_image':image,'plan_sha256':release.sha(FOLDER/'plan.json')})
        result=request('PATCH',WEB+API,data=plan['apply_body'])
        save('apply-ack',{'state':result.get('properties',{}).get('provisioningState'),'id':result.get('id')})
        print('Image-only website update submitted');return
    if stage!='verify' or actual!=image:raise ValueError('Unexpected installed image')
    props=current['properties'];revision='ibkrv2stg-web--'+REVISION
    if props['provisioningState']!='Succeeded' or props['latestReadyRevisionName']!=revision:
        print(json.dumps({'waiting':True,'state':props['provisioningState']}));return
    running=request('GET',WEB+'/revisions/'+revision+API)['properties']
    if running['healthState']!='Healthy' or not running['active']:raise ValueError('Revision not healthy')
    traffic=props['configuration']['ingress']['traffic']
    if len(traffic)!=1 or traffic[0]['weight']!=100 or traffic[0]['revisionName']!=revision:raise ValueError('Traffic mismatch')
    save('verified',{'status':'passed','image':image,'revision':revision,'authentication_preserved':True,'settings_preserved':True,
      'jobs_changed':False,'database_writes':0,'report_bytes_changed':False,'approvals_changed':False})
    print('Website image update verified; authentication and other settings unchanged')
if __name__=='__main__':main(sys.argv[1])
