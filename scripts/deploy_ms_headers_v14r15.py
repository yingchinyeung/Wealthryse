"""Image-only three-target layout cutover with protected, qualified rollback.

Does not register/release reports, run normal workers, change source or settings.
"""
import copy
import json
from pathlib import Path
import sys

import release_alpha_ms_headers_v14r15 as c
import build_ms_presentation_worker_candidate as build
import deploy_ms_presentation_worker_release as jobs
from deploy_metadata_monitor import BASE,request
from repair_alpha_ms_website_batch import web_invariant
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected

API='?api-version=2024-03-01'
WEB=BASE+'Microsoft.App/containerApps/ibkrv2stg-web'
FOLDER=c.FOLDER/'deployment-v1'
REVISION_SUFFIX='ms-headers-v14r15'
COMPLETION_STATUS='scoped_headers_deployed'
AUTHORIZATION='Owner explicitly approved scoped MS layout Azure update and historical regeneration'


def clean(value):
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if v is not None}
    if isinstance(value,list):return [clean(v) for v in value]
    return value


def web_hash(value):
    value=copy.deepcopy(value)
    for container in value['properties']['template']['containers']:
        for env in container.get('env',[]):
            if env.get('secretRef') and env.get('value')=='':env.pop('value')
    return web_invariant(value)


def read(folder,name):return json.loads((folder/(name+'.json')).read_bytes())


def save(name,record):
    build.save(FOLDER,name,{**record,'protected_receipt':retain(record,'ms-headers-v14r15-deploy-'+name)})


def verify_protected(record):
    actual,_=restore_protected(record['protected_receipt'])
    if actual!={k:v for k,v in record.items() if k!='protected_receipt'}:raise ValueError('Protected evidence differs')


def image(kind):
    output=read(c.FOLDER/kind,'build-completed')['output_images'][0]
    return output['registry']+'/'+output['repository']+'@'+output['digest']


def gates():
    cache_path=c.FOLDER/'release-gate-binding.json'
    if cache_path.exists():
        cached=read(c.FOLDER,'release-gate-binding');verify_protected(cached)
        for name,digest in cached['evidence_files_sha256'].items():
            if build.sha(c.FOLDER/name)!=digest:raise ValueError('Protected qualification binding changed: '+name)
        for kind in c.IMAGES:
            context=c.FOLDER/kind/'context';intent=read(c.FOLDER/kind,'source-intent')
            actual={p.relative_to(context).as_posix():build.sha(p) for p in context.rglob('*') if p.is_file()}
            if actual!=intent['files_sha256']:raise ValueError('Frozen context changed')
        return
    acceptance=read(c.FOLDER,'cloud-acceptance');verify_protected(acceptance)
    if acceptance['status']!='passed' or acceptance['cloud_pdf_checks']!=42 or not acceptance['website_worker_pdf_bytes_identical']:raise ValueError('Exact cloud acceptance required')
    for kind in c.IMAGES:
        folder=c.FOLDER/kind;intent=read(folder,'source-intent');context=folder/'context'
        actual={p.relative_to(context).as_posix():build.sha(p) for p in context.rglob('*') if p.is_file()}
        if actual!=intent['files_sha256']:raise ValueError('Frozen release files changed')
        native=read(folder,'native-build-qualified')
        if native['qualified'] is not True or native['linux_tests']!=65 or native['native_log_sha256']!=build.sha(folder/'native-build.log'):raise ValueError('Exact 65 native tests required')
        scan=read(folder,'scan-completed')
        protected,_=restore_protected(read(folder,'scan-protected-receipt'))
        if protected!=scan or scan['image']!=image(kind) or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']!=0:raise ValueError('Exact protected scan required')
        for mode in ('candidate','rollback'):
            proof=read(folder/(mode+'-runtime-v1'),'qualified');verify_protected(proof)
            expected=image(kind) if mode=='candidate' else c.IMAGES[kind]
            if proof['image']!=expected or proof['status']!='passed' or proof['count']!=21:raise ValueError('Candidate/rollback runtime gate failed')
    paths=['cloud-acceptance.json']
    for kind in c.IMAGES:
        paths += [kind+'/'+n for n in ('source-intent.json','local-qualification.json',
            'build-completed.json','native-build-qualified.json','native-build.log',
            'scan-completed.json','scan-protected-receipt.json')]
        paths += [kind+'/'+mode+'-runtime-v1/'+n for mode in ('candidate','rollback')
                  for n in ('qualified.json','verification.json')]
    record={'status':'passed','evidence_files_sha256':{n:build.sha(c.FOLDER/n) for n in paths},
        'verification':'Exact protected gate receipts verified once; each use re-verifies this protected composite and every frozen source/evidence hash'}
    build.save(c.FOLDER,'release-gate-binding',{**record,'protected_receipt':retain(record,'ms-headers-v14r15-exact-release-gate-binding')})


def main():
    stage=sys.argv[1]
    gates()
    if stage=='prepare':
        if FOLDER.exists():raise ValueError('Frozen deployment exists; inspect, never replace')
        jobs.window()
        if build.az('account','show','--query','id')!=build.SUB:raise ValueError('Unexpected subscription')
        records={}
        for name in (*build.JOBS,'ibkrv2stg-web'):
            is_web=name=='ibkrv2stg-web';kind='web' if is_web else 'worker'
            path=WEB if is_web else jobs.path(name)
            value=request('GET',path+API)
            if value['properties']['template']['containers'][0]['image']!=c.IMAGES[kind]:raise ValueError('Live baseline drift')
            if not is_web:jobs.scope(value,name);jobs.idle(name)
            template=clean(copy.deepcopy(value['properties']['template']))
            for container in template['containers']:
                for env in container.get('env',[]):
                    if env.get('secretRef') and env.get('value')=='':env.pop('value')
            rollback=copy.deepcopy(template);template['containers'][0]['image']=image(kind)
            if is_web:
                if value['properties']['configuration']['activeRevisionsMode']!='Single':raise ValueError('Only preserved Single revision policy supported')
                template['revisionSuffix']=REVISION_SUFFIX
            records[name]={'kind':kind,'path':path,'before':value,'image':image(kind),
                'invariant':web_hash(value) if is_web else jobs.settings_invariant(value),
                'apply_body':{'properties':{'template':template}},
                'rollback_body':{'properties':{'template':rollback}}}
        auth=request('GET',WEB+'/authConfigs/current'+API)
        FOLDER.mkdir()
        save('plan',{'targets':records,'auth_before':auth,'cloud_acceptance_sha256':build.sha(c.FOLDER/'cloud-acceptance.json'),
            'database_writes':0,'source_data_changed':False,'report_approvals_changed':False,
            'authorization':AUTHORIZATION})
        print(json.dumps({'prepared':True,'targets':3,'rollback_qualified':True}));return
    plan=read(FOLDER,'plan');verify_protected(plan)
    if plan['cloud_acceptance_sha256']!=build.sha(c.FOLDER/'cloud-acceptance.json'):raise ValueError('Accepted evidence changed')
    if stage in ('apply','verify'):
        name=sys.argv[2]
        if name not in plan['targets']:raise ValueError('Out of scope')
        target=plan['targets'][name];is_web=target['kind']=='web'
        current=request('GET',target['path']+API)
        invariant=web_hash(current) if is_web else jobs.settings_invariant(current)
        if invariant!=target['invariant']:raise ValueError('Non-image settings changed')
        if request('GET',WEB+'/authConfigs/current'+API)['properties']!=plan['auth_before']['properties']:raise ValueError('Authentication changed')
        actual=current['properties']['template']['containers'][0]['image']
        if stage=='apply':
            jobs.window()
            for job in build.JOBS:jobs.idle(job)
            if actual!=c.IMAGES[target['kind']]:raise ValueError('Expected baseline image required')
            save(name+'-apply-intent',{'from_image':actual,'to_image':target['image'],'body':target['apply_body'],
                'plan_sha256':build.sha(FOLDER/'plan.json')})
            result=request('PATCH',target['path']+API,data=target['apply_body'])
            save(name+'-apply-ack',{'id':result.get('id'),'provisioning_state':result.get('properties',{}).get('provisioningState')})
            print(json.dumps({'applied':name,'image_only':True}));return
        if actual!=target['image']:raise ValueError('Installed image mismatch')
        props=current['properties'];state=props['provisioningState']
        if state!='Succeeded':print(json.dumps({'waiting':name,'state':state}));return
        if is_web:
            revision=props['latestReadyRevisionName']
            if revision!='ibkrv2stg-web--'+REVISION_SUFFIX:print(json.dumps({'waiting':name,'revision':revision}));return
            running=request('GET',WEB+'/revisions/'+revision+API)['properties']
            if running['healthState']!='Healthy' or not running['active'] or running['runningState'] not in ('Running','RunningAtMaxScale'):raise ValueError('Website not healthy')
            traffic=props['configuration']['ingress']['traffic']
            if len(traffic)!=1 or traffic[0]['weight']!=100 or traffic[0].get('revisionName')!=revision:raise ValueError('Traffic verification failed')
        save(name+'-qualified',{'status':'passed','image':actual,'settings_preserved':True,'authentication_preserved':True})
        print(json.dumps({'verified':name,'settings_preserved':True}));return
    if stage!='seal':raise ValueError('Unknown stage')
    for name,target in plan['targets'].items():
        proof=read(FOLDER,name+'-qualified');verify_protected(proof)
        if proof['status']!='passed' or proof['image']!=target['image']:raise ValueError('Target verification incomplete')
    save('completion',{'status':COMPLETION_STATUS,'targets':{n:r['image'] for n,r in plan['targets'].items()},
        'layout_version':c.LAYOUT,'rollback_runtime_qualified':True,'database_writes':0,
        'source_data_changed':False,'report_approvals_changed':False,
        'historical_registration_complete':False,'website_download_verification_complete':False})
    print(json.dumps({'status':COMPLETION_STATUS,'targets':3,'historical_registration_complete':False}))


if __name__=='__main__':main()
