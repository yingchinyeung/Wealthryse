"""Two-job, image-only MS release. No normal worker, DB, report or web writes.

Each mutation is create-only and one-shot. Unknown outcomes are inspected, never
redispatched. Pre-read drift checks are not an atomic distributed execution lock.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import build_ms_presentation_worker_candidate as build
from deploy_metadata_monitor import BASE, request
from finish_ms_reporting_recovery_qualification import validate as validate_recovery
from qualify_ms_presentation_worker_runtime import API, candidate_gate, WORKBOOK_CONTRACT
from rehearse_ms_worker_image_rollback import restore_protected, settings_invariant as rehearsed_settings_invariant
from run_specialist_gateway_read_verification import retain
from scan_ms_presentation_worker_candidate import verify

TERMINAL = {'Succeeded', 'Failed', 'Stopped', 'Canceled', 'Cancelled'}
ENTRYPOINTS = dict(zip(build.JOBS, ('scripts/run_self_service_onboarding_reports.py',
                                  'scripts/run_registry_month_end_packages.py')))
CHECK = """import hashlib,json,sys
from pathlib import Path
sys.path[:0]=['/app/src','/app/scripts']
def require(ok):
 if not ok: raise ValueError('Installed worker identity check failed')
for name,digest in BINDING['installed_files_sha256'].items():
 require(hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()==digest)
r=json.loads(Path('/app/MS_PRESENTATION_WORKER_RELEASE.json').read_bytes())
require(r['content_contract_version']==BINDING['content_contract_version'] and r['chart_workbooks_required'] is True)
require(r['source_data_changed'] is False and r['report_release_authorized'] is False and r['onboarding_activated'] is False and r['website_cutover'] is False)
from ibkr_etl.monthly_artifacts import factsheet_layout_version
require(factsheet_layout_version('alpha-world-ms')==BINDING['layout_version'])
import generate_month_end_package as m,render_onboarding_historical_reports as h
import run_self_service_onboarding_reports,run_registry_month_end_packages
from validate_subject_presentation import validate_subject_presentation as v
require(m.validate_subject_presentation is v and h.validate_subject_presentation is v)
print('MS_INSTALLED_WORKER_CHECK '+json.dumps({'status':'passed','image':BINDING['image'],'files_verified':len(BINDING['installed_files_sha256']),'normal_entrypoints_invoked':0,'database_connections':0}),flush=True)
"""


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read(folder, name):
    return json.loads((folder/(name+'.json')).read_bytes())


def settings_invariant(job):
    # Previously qualified Azure defaults only. The actual baseline is archived
    # without normalization. Credentials, nonempty values and all other settings
    # remain exact; the configuration/identity are never included in the PATCH.
    result=copy.deepcopy(job)
    for container in result['properties']['template']['containers']:
        for env in container.get('env',[]):
            if env.get('secretRef') and env.get('value')=='':
                env.pop('value')
    return rehearsed_settings_invariant(result)


def path(name):
    require(name in build.JOBS, 'Only the two approved reporting jobs are in scope')
    return BASE+'Microsoft.App/jobs/'+name


def scope(job, name):
    require(job.get('name')==name and job.get('id','').casefold()==path(name).casefold(), 'Job ARM identity differs')
    p=job['properties'];cfg=p['configuration'];containers=p['template']['containers']
    require(len(containers)==1 and not p['template'].get('initContainers')
            and not p['template'].get('volumes'), 'Single volume-free reporting worker required')
    require(containers[0].get('command')==['python'] and containers[0].get('args')==[ENTRYPOINTS[name]],
            'Normal persisted reporting command differs')
    require(not any(s.get('value') for s in cfg.get('secrets',[])), 'Inline secret values prohibited')
    identities={k.casefold() for k in job.get('identity',{}).get('userAssignedIdentities',{})}
    require(job.get('identity',{}).get('type')=='UserAssigned' and identities, 'Existing user identity required')
    for secret in cfg.get('secrets',[]):
        require(secret.get('keyVaultUrl') and str(secret.get('identity','')).casefold() in identities,
                'Existing Key Vault identity references required')
    db=[e for e in containers[0].get('env',[]) if e['name']=='DATABASE_URL']
    require(len(db)==1 and db[0].get('secretRef') and not db[0].get('value'), 'Database secret reference required')
    trigger='Manual' if name==build.JOBS[0] else 'Schedule'
    require(cfg['triggerType']==trigger and not cfg.get('eventTriggerConfig'), 'Trigger type differs')
    if trigger=='Schedule':
        require(cfg['scheduleTriggerConfig']['cronExpression']=='0 9 * * *', 'Qualified daily schedule differs')


def window(now=None):
    now=now or datetime.now(timezone.utc)
    minute=now.hour*60+now.minute
    require(not 8*60<=minute<9*60+30, 'Release withheld around the 09:00 UTC daily schedule')


def idle(name):
    target=path(name)+'/executions';next_path=target+API
    for _ in range(20):
        data=request('GET',next_path)
        require(all(r['properties']['status'] in TERMINAL for r in data.get('value',[])),
                'Active or unknown reporting execution; do not cancel or replace it')
        next_link=data.get('nextLink')
        if not next_link:
            return
        parsed=urlsplit(next_link)
        require(parsed.scheme=='https' and parsed.netloc=='management.azure.com'
                and parsed.path.casefold()==target.casefold() and parsed.query,
                'Unexpected execution pagination target')
        next_path=parsed.path+'?'+parsed.query
    raise ValueError('Execution pagination exceeds bounded inspection')


def image_evidence(folder):
    source,_,output=verify(folder)
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=read(folder,'scan-completed');receipt=read(folder,'scan-protected-receipt')
    digest=hashlib.sha256(json.dumps(scan,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    require(scan['image']==image and scan['assessment']['status']=='pass'
            and scan['assessment']['blocking_findings']==0 and receipt['sha256']==digest
            and receipt['protected_readback_verified'] is True and receipt.get('version_id')
            and build.sha(folder/'scan-v1/vulnerabilities.json')==scan['assessment']['report_sha256']
            and build.sha(folder/'scan-v1/sbom.cdx.json')==scan['assessment']['sbom_sha256'], 'Frozen security gate differs')
    validate_recovery(folder,image)
    recovery=read(folder/'runtime-v1','recovery-acceptance')
    require(recovery['status']=='reporting_fallback_and_isolated_recovery_qualified'
            and recovery['image']==image and recovery['candidate_acceptance_sha256']==build.sha(folder/'runtime-v1/candidate-render-acceptance.json')
            and recovery['fallback_qualification_sha256']==build.sha(folder/'runtime-v1/rollback-qualified.json')
            and recovery['isolated_recovery_sha256']==build.sha(folder/'rollback-rehearsal-v1/acceptance.json'),
            'Bounded recovery evidence differs')
    require(build.az('account','show','--query','id')==build.SUB, 'Azure subscription differs')
    return source,image,recovery


def binding(folder, image):
    base_path=build.ROOT/'.work/alpha-ms-distribution-v14r13-20261007/context/ALPHA_MS_HKD_RELEASE.json'
    base=json.loads(base_path.read_bytes())
    release_path=folder/'context/MS_PRESENTATION_WORKER_RELEASE.json'
    release=json.loads(release_path.read_bytes())
    require(release['content_contract_version']==WORKBOOK_CONTRACT and release['chart_workbooks_required'] is True,
            'Mandatory editable workbook contract required')
    files={**base['installed_files_sha256'],**release['installed_files_sha256'],
           'ALPHA_MS_HKD_RELEASE.json':build.sha(base_path),
           'MS_PRESENTATION_WORKER_RELEASE.json':build.sha(release_path),
           'templates/U16088571/approved-august-2026-20-slide-v24.pptx':
                '4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191'}
    return {'image':image,'installed_files_sha256':files,'content_contract_version':WORKBOOK_CONTRACT,
            'layout_version':base['layout_version']}


def patch_body(job, image):
    template=copy.deepcopy(job['properties']['template'])
    require(len(template['containers'])==1, 'Single worker required')
    template['containers'][0]['image']=image
    return {'properties':{'template':template}}


def checked_job(stage, plan, name, expected_images):
    job=request('GET',path(name)+API);scope(job,name)
    require(job['properties']['provisioningState']=='Succeeded'
            and settings_invariant(job)==plan['settings_invariants'][name]
            and job['properties']['template']['containers'][0]['image'] in expected_images,
            'Image or non-image settings drifted: '+name)
    return job


def all_jobs(stage, plan):
    result={}
    for name in build.JOBS:
        images={plan['base_image']}
        if (stage/(name+'-apply-intent.json')).exists():
            images.add(plan['image'])
        if (stage/(name+'-apply-qualified.json')).exists() and not (stage/(name+'-rollback-intent.json')).exists():
            images={plan['image']}
        result[name]=checked_job(stage,plan,name,images)
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=('prepare','apply','verify','check','inspect-check','rollback','verify-rollback','seal'))
    parser.add_argument('--folder',type=Path,required=True)
    parser.add_argument('--job',choices=build.JOBS)
    parser.add_argument('--authorize-scoped-worker-release',action='store_true')
    args=parser.parse_args();folder=args.folder.resolve();stage=folder/'deployment-v1'
    source,image,recovery=image_evidence(folder)
    if args.stage=='prepare':
        require(args.authorize_scoped_worker_release, 'Explicit scoped release authority required')
        require(not stage.exists(), 'Preserve existing deployment attempt')
        candidate_gate(folder);window()
        recovered,_=restore_protected(recovery['protected_receipt'])
        require(recovered=={k:v for k,v in recovery.items() if k!='protected_receipt'}, 'Protected recovery record differs')
        old_plan=read(folder/'rollback-rehearsal-v1','plan')
        old,_=restore_protected(old_plan['protected_receipt'])
        require(old=={k:v for k,v in old_plan.items() if k!='protected_receipt'}, 'Protected prior ARM baseline differs')
        jobs={}
        for name in build.JOBS:
            idle(name);j=request('GET',path(name)+API);scope(j,name)
            require(j['properties']['provisioningState']=='Succeeded'
                    and j['properties']['template']['containers'][0]['image']==source['base_image']
                    and build.invariant(j)==build.invariant(old['before_live_jobs'][name]), 'ARM baseline drifted')
            jobs[name]=j
        b=binding(folder,image)
        record={'image':image,'base_image':source['base_image'],'before_jobs':jobs,
            'settings_invariants':{n:settings_invariant(j) for n,j in jobs.items()},
            'binding':b,'check_source':'BINDING='+repr(b)+'\n'+CHECK,
            'controller_sha256':build.sha(Path(__file__)),
            'evidence_sha256':{n:build.sha(folder/n) for n in ('source-intent.json','scan-completed.json',
                'runtime-v1/recovery-acceptance.json','runtime-v1/candidate-render-acceptance.json')},
            'authorization':'Owner authorized scoped two-reporting-job image release; no report approval or onboarding',
            'utc_prepared_at':datetime.now(timezone.utc).isoformat(),'targets':list(build.JOBS),
            'website_updates':False,'database_connections':0,'reports_released':False}
        stage.mkdir()
        build.save(stage,'plan',{**record,'protected_receipt':retain(record,folder.name+'-scoped-worker-release-plan')})
        print(json.dumps({'status':'prepared','targets':list(build.JOBS),'database_connections':0}),flush=True)
        return
    plan=read(stage,'plan')
    require(plan['image']==image and plan['base_image']==source['base_image']
            and plan['controller_sha256']==build.sha(Path(__file__))
            and plan['targets']==list(build.JOBS)
            and all(build.sha(folder/n)==h for n,h in plan['evidence_sha256'].items()), 'Frozen deployment intent differs')
    recovered,_=restore_protected(plan['protected_receipt'])
    require(recovered=={k:v for k,v in plan.items() if k!='protected_receipt'}, 'Protected deployment plan differs')
    jobs=all_jobs(stage,plan)
    if args.stage=='seal':
        checks={}
        for name in build.JOBS:
            proof=read(stage,name+'-check-qualified')
            require(proof['status']=='passed' and proof['image']==image
                    and not (stage/(name+'-rollback-intent.json')).exists(), 'Both installed checks must pass without rollback')
            require(jobs[name]['properties']['template']['containers'][0]['image']==image, 'Candidate not installed')
            checks[name]=proof
        record={'status':'scoped_reporting_workers_deployed_and_identity_verified','image':image,
            'rollback_image':plan['base_image'],'jobs':list(build.JOBS),'installed_checks':checks,
            'non_image_settings_preserved':True,'credential_references_preserved':True,
            'deployment_plan_sha256':build.sha(stage/'plan.json'),
            'recovery_acceptance_sha256':build.sha(folder/'runtime-v1/recovery-acceptance.json'),
            'database_connections':0,'database_writes':0,'ibkr_source_data_changed':False,
            'reports_released':False,'onboarding_activated':False,'website_batch_registered':False,
            'full_live_image_switch_rollback_exercised':False,
            'normal_worker_processing_invoked':False,'new_historical_batch_generated':False,
            'next_gate':'Generate and register the new private 21-month historical batch, then verify website downloads'}
        build.save(stage,'completion',{**record,'protected_receipt':retain(record,folder.name+'-installed-worker-release')})
        print(json.dumps({'status':record['status'],'reporting_jobs':2,'database_connections':0}),flush=True)
        return
    require(args.job, 'One explicit reporting target required');name=args.job
    label=name+'-'+('apply' if args.stage in ('apply','verify') else 'rollback' if 'rollback' in args.stage else 'check')
    if args.stage in ('apply','rollback'):
        require(not (stage/(label+'-intent.json')).exists(), 'Mutation attempted; inspect rather than repeat')
        window()
        for n in build.JOBS:idle(n)
        job=checked_job(stage,plan,name,{plan['base_image'] if args.stage=='apply' else image})
        if args.stage=='apply' and name==build.JOBS[1]:
            require(read(stage,build.JOBS[0]+'-check-qualified')['status']=='passed', 'Manual worker must pass before scheduled worker')
        target=image if args.stage=='apply' else plan['base_image']
        body=patch_body(plan['before_jobs'][name],target)
        record={'target':path(name),'from_image':job['properties']['template']['containers'][0]['image'],
                'to_image':target,'body':body,'settings_invariant':plan['settings_invariants'][name]}
        build.save(stage,label+'-intent',{**record,'protected_receipt':retain(record,folder.name+'-'+label+'-intent')})
        # Re-check drift and executions immediately before the single PATCH.
        for n in build.JOBS:idle(n)
        all_jobs(stage,plan);checked_job(stage,plan,name,{record['from_image']});window()
        request('PATCH',path(name)+API,data=body)
    elif args.stage in ('verify','verify-rollback'):
        intent=read(stage,label+'-intent')
        target=image if args.stage=='verify' else plan['base_image']
        require(intent['target']==path(name) and intent['to_image']==target
                and intent['body']==patch_body(plan['before_jobs'][name],target)
                and intent['settings_invariant']==plan['settings_invariants'][name], 'Mutation intent identity differs')
        archived,_=restore_protected(intent['protected_receipt'])
        require(archived=={k:v for k,v in intent.items() if k!='protected_receipt'}, 'Protected mutation intent differs')
        job=checked_job(stage,plan,name,{intent['to_image']})
        build.save(stage,label+'-qualified',{'status':'passed','image':intent['to_image'],
            'settings_preserved':True,'job':job})
    elif args.stage=='check':
        require(read(stage,name+'-apply-qualified')['status']=='passed', 'Persistent image update must be verified')
        require(not (stage/(label+'-intent.json')).exists(), 'Check dispatched; inspect rather than repeat')
        window()
        for n in build.JOBS:idle(n)
        job=checked_job(stage,plan,name,{image})
        template=copy.deepcopy(job['properties']['template'])
        template['containers'][0].update(command=['python'],args=['-c',plan['check_source']])
        record={'image':image,'template':template,'source_sha256':hashlib.sha256(plan['check_source'].encode()).hexdigest(),
                'normal_entrypoints_invoked':0,'database_connections':0}
        build.save(stage,label+'-intent',{**record,'protected_receipt':retain(record,folder.name+'-'+label+'-intent')})
        for n in build.JOBS:idle(n)
        all_jobs(stage,plan);window()
        run=request('POST',path(name)+'/start'+API,data=template)
        require(run.get('name'), 'Unknown check dispatch; do not retry')
        build.save(stage,label+'-dispatch',{'execution':run['name'],'image':image})
    elif args.stage=='inspect-check':
        ack=read(stage,label+'-dispatch');intent=read(stage,label+'-intent')
        require(re.fullmatch(re.escape(name)+'-[a-z0-9]{7}',ack['execution']) and ack['image']==image,
                'Execution identity differs')
        archived,_=restore_protected(intent['protected_receipt'])
        require(archived=={k:v for k,v in intent.items() if k!='protected_receipt'}
                and intent['image']==image
                and intent['source_sha256']==hashlib.sha256(plan['check_source'].encode()).hexdigest()
                and intent['template']['containers'][0]['command']==['python']
                and intent['template']['containers'][0]['args']==['-c',plan['check_source']], 'Protected installed check differs')
        run=request('GET',path(name)+'/executions/'+ack['execution']+API)
        status=run['properties']['status'];print(json.dumps({'job':name,'execution':ack['execution'],'status':status}),flush=True)
        if status!='Succeeded':return
        actual=run['properties']['template']['containers'];expected=intent['template']['containers']
        require(len(actual)==len(expected)==1, 'Actual check container count differs')
        for k in ('image','command','args','name'):
            require(actual[0].get(k)==expected[0].get(k), 'Actual installed command or image differs')
        build.save(stage,label+'-qualified',{'status':'passed','image':image,'execution':ack['execution'],
            'installed_files_verified':len(plan['binding']['installed_files_sha256']),
            'normal_entrypoints_invoked':0,'database_connections':0,'persistent_settings_preserved':True})
    print(json.dumps({'stage':args.stage,'job':name,'database_connections':0}),flush=True)


if __name__=='__main__':
    main()
