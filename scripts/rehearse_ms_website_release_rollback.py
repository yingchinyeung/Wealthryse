"""Inert, credential-free scratch image switches; never mutate live services."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

import qualify_ms_website_native_runtime as qualified
import rehearse_ms_worker_image_rollback as old
from repair_alpha_ms_website_batch import web_invariant

p=qualified.probe
FOLDER=p.ROOT/'.work/alpha-ms-website-release-20261008-v1'
STAGE=FOLDER/'image-rollback-v1'
NAMES={'web':'ibkrv2stg-ms-web-rb-20261008','benchmark':'ibkrv2stg-ms-bench-rb-20261008'}
AUTH_ENV={'AUTHORIZATION_MODE','ALLOWED_EMAILS','ALLOWED_ENTRA_OBJECT_IDS',
    'ONBOARDING_OPERATOR_EMAILS','ONBOARDING_OPERATOR_ENTRA_OBJECT_IDS',
    'VIEWER_EMAILS','VIEWER_ENTRA_OBJECT_IDS','PORTFOLIO_TENANT_ID','RELEASE_CHANNEL'}
PURPOSE='alpha-ms-website-benchmark-isolated-rollback'
CHECK='''import hashlib,json,os,sys
from pathlib import Path
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl import provisional_benchmark
if os.environ['MS_ROLLBACK_KIND']=='web':
 from ibkr_etl import web
 from fastapi.testclient import TestClient
 from unittest.mock import patch
 with patch.object(web,'_database',side_effect=AssertionError('Database access prohibited in inert rollback check')):
  with TestClient(web.app) as client:
   response=client.get('/api/portfolio/alpha-world-ms/benchmark-comparison',follow_redirects=False)
   if response.status_code!=403:raise ValueError('Anonymous read must stay denied')
release=Path('/app/MS_WEBSITE_BENCHMARK_RELEASE.json')
if release.exists():
 r=json.loads(release.read_bytes())
 for section in ('approved_report_files_sha256','website_files_sha256'):
  for name,digest in r[section].items():
   if hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()!=digest:
    raise ValueError('Candidate integrity differs')
print('MS_WEBSITE_INERT_ROLLBACK_CHECK_PASSED',flush=True)
'''


def read(name):return p.release.read(STAGE,name)
def path(kind):return p.release.BASE+'Microsoft.App/jobs/'+NAMES[kind]
def live(kind):return p.request('GET',path(kind)+p.release.API)


def require(ok,message):
    if not ok:raise ValueError(message)


def clean_reference(job):
    result=old.reference_settings(old.clean(copy.deepcopy(job)))
    return result


def definition(base,kind):
    manual=base['manual_job'];web=base['website']
    identity=manual['identity'];require(identity['type']=='UserAssigned','Existing assigned identity required')
    env=[copy.deepcopy(e) for e in web['properties']['template']['containers'][0]['env'] if e['name'] in AUTH_ENV]
    require(all(not e.get('secretRef') for e in env),'No secret references in scratch environment')
    env.append({'name':'MS_ROLLBACK_KIND','value':kind})
    original=base['website' if kind=='web' else 'benchmark_job']
    registries=old.clean(copy.deepcopy(manual['properties']['configuration']['registries']))
    for registry in registries:
        require(registry.get('identity') and not registry.get('passwordSecretRef'),'Managed registry identity required')
        registry.pop('passwordSecretRef',None);registry.pop('username',None)
    return {'location':manual['location'],
        'identity':{'type':'UserAssigned','userAssignedIdentities':{k:{} for k in identity['userAssignedIdentities']}},
        'tags':{'purpose':PURPOSE,'scope':kind,'expiresOn':'2026-10-08'},
        'properties':{'environmentId':manual['properties']['environmentId'],
            'configuration':{'triggerType':'Manual','replicaTimeout':180,'replicaRetryLimit':0,
                'manualTriggerConfig':{'parallelism':1,'replicaCompletionCount':1},'registries':registries,'secrets':[]},
            'template':{'containers':[{'name':'rollback-check',
                'image':original['properties']['template']['containers'][0]['image'],
                'command':['python'],'args':['-c',CHECK],'env':env,'resources':{'cpu':0.25,'memory':'0.5Gi'}}]}}}


def non_image(job):return old.settings_invariant(clean_reference(job))


def scope(job,kind):
    require(job['id'].casefold()==path(kind).casefold() and job['name']==NAMES[kind]
        and job.get('tags',{}).get('purpose')==PURPOSE,'Wrong scratch target')
    c=job['properties']['configuration'];containers=job['properties']['template']['containers']
    require(c['triggerType']=='Manual' and c['replicaRetryLimit']==0
        and c['manualTriggerConfig']=={'parallelism':1,'replicaCompletionCount':1}
        and not c.get('scheduleTriggerConfig') and not c.get('eventTriggerConfig')
        and not c.get('secrets') and len(containers)==1
        and containers[0]['command']==['python'] and containers[0]['args']==['-c',CHECK]
        and all(not e.get('secretRef') and e['name'] in AUTH_ENV|{'MS_ROLLBACK_KIND'} for e in containers[0]['env'])
        and len({e['name'] for e in containers[0]['env']})==len(containers[0]['env'])
        and next((e.get('value') for e in containers[0]['env'] if e['name']=='MS_ROLLBACK_KIND'),None)==kind
        and not job['properties']['template'].get('volumes')
        and not job['properties']['template'].get('initContainers'),'Scratch scope drift')


def live_unchanged(base):
    web=p.request('GET',base['website']['id']+p.release.API)
    auth=p.request('GET',base['website']['id']+'/authConfigs/current'+p.release.API)
    require(web_invariant(web)==web_invariant(base['website'])
        and web['properties']['template']['containers'][0]['image']==base['website']['properties']['template']['containers'][0]['image']
        and auth['properties']==base['website_auth']['properties'],'Live website/auth drift')
    for name in ('benchmark_job','manual_job'):
        job=p.request('GET',base[name]['id']+p.release.API)
        require(old.settings_invariant(job)==old.settings_invariant(base[name])
            and job['properties']['template']['containers'][0]['image']==base[name]['properties']['template']['containers'][0]['image'],
            'Live job drift: '+name)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','create','capture','switch',
        'verify-switch','smoke-candidate','inspect-candidate','restore','verify-restore','smoke-base','inspect-base',
        'cleanup','verify-cleanup','seal'));parser.add_argument('--kind',choices=tuple(NAMES))
    args=parser.parse_args();base=p.release.read(FOLDER,'baseline');p.verify_protected(base)
    _,image=qualified.image_gate()
    runtime=p.release.read(qualified.STAGE,'qualified');p.verify_protected(runtime)
    require(runtime['image']==image and runtime['status']=='native_candidate_postgres_and_readers_passed','Native runtime gate failed')
    live_unchanged(base)
    if args.stage=='prepare':
        require(not STAGE.exists(),'Preserve prior rehearsal')
        definitions={k:definition(base,k) for k in NAMES}
        for k in NAMES:require(p.request('GET',path(k)+p.release.API,missing=True) is None,'Scratch already exists')
        STAGE.mkdir()
        plan={'definitions':definitions,'image':image,'baseline_sha256':p.sha(FOLDER/'baseline.json'),
            'controller_sha256':p.sha(Path(__file__)),'live_mutations':0,'database_connections':0}
        p.release.build.save(STAGE,'plan',{**plan,'protected_receipt':p.retain(plan,'ms-website-image-rollback-v1-plan')})
        print(json.dumps({'status':'prepared','no_database_credentials':True,'live_mutations':0}),flush=True);return
    plan=read('plan');p.verify_protected(plan)
    require(plan['controller_sha256']==p.sha(Path(__file__)) and plan['baseline_sha256']==p.sha(FOLDER/'baseline.json')
        and plan['image']==image,'Frozen rehearsal changed')
    if args.stage=='seal':
        for kind in NAMES:
            for label in ('switch-qualified','candidate-qualified','restore-qualified','base-qualified','cleanup-qualified'):
                require(read(kind+'-'+label)['status']=='passed','Incomplete rollback proof')
        proof={'status':'isolated_website_and_benchmark_image_rollback_passed','image':image,
            'plan_sha256':p.sha(STAGE/'plan.json'),'live_mutations':0,'database_connections':0,
            'scratch_resources_removed':True,'website_auth_preserved':True,'full_live_rollback_exercised':False,
            'scope':'Real scratch ARM image changes and exact protected baseline recovery; inert runtime and anonymous denial checks'}
        p.release.build.save(STAGE,'acceptance',{**proof,'protected_receipt':p.retain(proof,'ms-website-image-rollback-v1-acceptance')})
        print(json.dumps(proof),flush=True);return
    require(args.kind in NAMES,'Exact scratch kind required');kind=args.kind;prefix=kind+'-'
    wanted=plan['definitions'][kind];base_image=wanted['properties']['template']['containers'][0]['image']
    if args.stage=='create':
        require(not (STAGE/(prefix+'create-intent.json')).exists(),'Inspect rather than repeat creation')
        require(p.request('GET',path(kind)+p.release.API,missing=True) is None,'Refuse overwrite')
        p.release.build.save(STAGE,prefix+'create-intent',{'target':path(kind),'definition':wanted})
        p.request('PUT',path(kind)+p.release.API,data=wanted)
    elif args.stage=='capture':
        job=live(kind);scope(job,kind)
        if job['properties']['provisioningState']!='Succeeded':print(json.dumps({'status':'waiting'}));return
        require(non_image(job)==non_image(wanted) and job['properties']['template']['containers'][0]['image']==base_image,'Scratch projection differs')
        snapshot={'job':job,'image':base_image,'non_image_sha256':non_image(job)}
        p.release.build.save(STAGE,prefix+'baseline',{**snapshot,'protected_receipt':p.retain(snapshot,'ms-website-'+kind+'-rollback-baseline-v1')})
    elif args.stage in ('switch','restore'):
        label=args.stage;before=base_image if label=='switch' else image;after=image if label=='switch' else base_image
        require(not (STAGE/(prefix+label+'-intent.json')).exists(),'Inspect rather than repeat image update')
        snapshot=read(prefix+'baseline');p.verify_protected(snapshot)
        job=live(kind);scope(job,kind)
        runs=p.request('GET',path(kind)+'/executions'+p.release.API).get('value',[])
        require(not any(r['properties']['status'] in old.ACTIVE for r in runs),'Scratch still running')
        require(non_image(job)==snapshot['non_image_sha256'] and job['properties']['template']['containers'][0]['image']==before,'Scratch drift')
        if label=='restore':require(read(prefix+'candidate-qualified')['status']=='passed','Candidate inert check required')
        recovered,_=p.restore_protected(snapshot['protected_receipt'])
        require(recovered['job']==snapshot['job'],'Protected rollback definition differs')
        template=copy.deepcopy(recovered['job']['properties']['template']);template['containers'][0]['image']=after
        intent={'target':path(kind),'from_image':before,'to_image':after,'template':template,
            'protected_baseline_version':snapshot['protected_receipt']['version_id']}
        p.release.build.save(STAGE,prefix+label+'-intent',intent)
        p.request('PATCH',path(kind)+p.release.API,data={'properties':{'template':template}})
    elif args.stage in ('verify-switch','verify-restore'):
        label=args.stage.removeprefix('verify-');job=live(kind);scope(job,kind)
        if job['properties']['provisioningState']!='Succeeded':print(json.dumps({'status':'waiting'}));return
        require(non_image(job)==read(prefix+'baseline')['non_image_sha256']
            and job['properties']['template']['containers'][0]['image']==read(prefix+label+'-intent')['to_image'],'Image or settings differs')
        p.release.build.save(STAGE,prefix+label+'-qualified',{'status':'passed','image':job['properties']['template']['containers'][0]['image']})
    elif args.stage.startswith('smoke-'):
        mode=args.stage.removeprefix('smoke-');require(not (STAGE/(prefix+mode+'-dispatch-intent.json')).exists(),'Do not redispatch smoke')
        proof=read(prefix+('switch-qualified' if mode=='candidate' else 'restore-qualified'))
        job=live(kind);scope(job,kind)
        require(non_image(job)==read(prefix+'baseline')['non_image_sha256'] and job['properties']['template']['containers'][0]['image']==proof['image'],'Smoke drift')
        p.release.build.save(STAGE,prefix+mode+'-dispatch-intent',{'image':proof['image'],'database_connections':0})
        ack=p.request('POST',path(kind)+'/start'+p.release.API)
        require(ack.get('name'),'Unknown dispatch; do not repeat')
        p.release.build.save(STAGE,prefix+mode+'-dispatch',{'execution':ack['name'],'image':proof['image']})
    elif args.stage.startswith('inspect-'):
        mode=args.stage.removeprefix('inspect-');ack=read(prefix+mode+'-dispatch')
        run=p.request('GET',path(kind)+'/executions/'+ack['execution']+p.release.API)
        status=run['properties']['status'];print(json.dumps({'execution':ack['execution'],'status':status}),flush=True)
        if status!='Succeeded':return
        c=run['properties']['template']['containers'];require(len(c)==1 and c[0]['image']==ack['image']
            and c[0]['command']==['python'] and c[0]['args']==['-c',CHECK]
            and not any(e.get('secretRef') for e in c[0]['env']),'Executed command/image differs')
        p.release.build.save(STAGE,prefix+mode+'-qualified',{'status':'passed','image':ack['image'],'database_connections':0})
    elif args.stage=='cleanup':
        require(read(prefix+'base-qualified')['status']=='passed','Restored baseline smoke required')
        require(not (STAGE/(prefix+'cleanup-intent.json')).exists(),'Inspect rather than repeat delete')
        job=live(kind);scope(job,kind)
        runs=p.request('GET',path(kind)+'/executions'+p.release.API).get('value',[])
        require(not any(r['properties']['status'] in old.ACTIVE for r in runs),'Scratch busy')
        require(non_image(job)==read(prefix+'baseline')['non_image_sha256'] and job['properties']['template']['containers'][0]['image']==base_image,'Scratch not restored')
        p.release.build.save(STAGE,prefix+'cleanup-intent',{'exact_target':path(kind),'restored':True})
        p.request('DELETE',path(kind)+p.release.API)
    elif args.stage=='verify-cleanup':
        require(read(prefix+'cleanup-intent')['exact_target']==path(kind),'Wrong removal scope')
        require(p.request('GET',path(kind)+p.release.API,missing=True) is None,'Scratch still removing')
        p.release.build.save(STAGE,prefix+'cleanup-qualified',{'status':'passed','scratch_absent':True})
    print(json.dumps({'stage':args.stage,'kind':kind,'live_mutations':0}),flush=True)


if __name__=='__main__':main()
