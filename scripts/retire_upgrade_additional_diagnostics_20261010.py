"""Owner-authorized retirement of exactly seven additional diagnostic jobs.

Archive each definition/execution first. Never retire operating reporting,
onboarding, import, verification, inbox or scheduled jobs. No identity deletion.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib,json,sys
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from plan_upgrade_owner_retirement_20261010 import listing,refs,bindings,assert_manual_idle,BASE
from inspect_upgrade_owner_payloads_20261010 import decode
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage20/diagnostic-retirement-v1'
API='?api-version=2026-07-01'
APPROVED=frozenset(('ibkrv2stg-ownmid-20261005','ibkrv2stg-ownmod2-20261005','ibkrv2stg-ownmodel-20261005',
                   'ibkrv2stg-ownrmeta-20261006','ibkrv2stg-ownrtree-20261006','ibkrv2stg-ownrtree2-20261006',
                   'ibkrv2stg-ownertrial-log-canary'))
SYNTHETIC='ibkrv2stgauhecwc4itaoo.azurecr.io/codex-synthetic-runtime@sha256:6511542c49001ba34c2639d4a90d6d3ea28ab1c50cee5be05dc09bd414d34748'
CANARY='python:3.12-slim-bookworm@sha256:9901e0a8d75037d8242ed43155cbcb2d1f61be1356383d8054afb59fd50e39c4'


def path_for(name):
    if name not in APPROVED:raise ValueError('Outside exact seven-job authorization')
    return rid('Microsoft.App/jobs',name)


def expected_image(name):
    path_for(name)
    return CANARY if name=='ibkrv2stg-ownertrial-log-canary' else SYNTHETIC


def no_inline_secrets(job):
    if any(s.get('value') for s in job['properties'].get('configuration',{}).get('secrets') or []):
        raise ValueError('Inline credential cannot be archived')
    for c in job['properties'].get('template',{}).get('containers',[]):
        for e in c.get('env') or []:
            if e.get('value') and any(k in e['name'].upper() for k in ('PASSWORD','TOKEN','SECRET','API_KEY','DATABASE_URL')):
                raise ValueError('Inline runtime credential cannot be archived')


def restore_body(job):
    name=job['name'];path_for(name);p=job['properties'];identity=job.get('identity',{})
    if identity.get('type')=='None':wanted={'type':'None'}
    elif identity.get('type')=='UserAssigned':wanted={'type':'UserAssigned','userAssignedIdentities':{i:{} for i in identity['userAssignedIdentities']}}
    else:raise ValueError('Unreviewed identity lifecycle; do not retire')
    if len(p['template']['containers'])!=1 or p['template']['containers'][0]['image']!=expected_image(name):
        raise ValueError('Unexpected frozen image')
    return {'location':job['location'],'tags':job.get('tags',{}),'identity':wanted,
            'properties':{k:p[k] for k in ('environmentId','workloadProfileName','configuration','template') if k in p and p[k] is not None}}


def capture_one(name):
    folder=OUT/name
    if (folder/'baseline.json').exists():
        baseline=protected(folder/'baseline.json');current_target(baseline);return baseline
    path=path_for(name);job=request('GET',path+API)
    runs=listing(path+'/executions','2026-07-01');assert_manual_idle(job,runs);no_inline_secrets(job)
    details=[]
    for run in runs:
        value=request('GET',path+'/executions/'+run['name']+API);no_inline_secrets(value);details.append(value)
    record={'name':name,'id':path,'definition':job,'invariant':settings_invariant(job),'image':expected_image(name),
        'executions':details,'role_assignments':listing(path+'/providers/Microsoft.Authorization/roleAssignments','2022-04-01'),
        'restore_body':restore_body(job),'captured_at':datetime.now(timezone.utc).isoformat()}
    if len(json.dumps(record).encode())>1800000:raise ValueError('Per-job archive exceeds bounded recovery reader')
    save(folder,'baseline',record);print(json.dumps({'archived':name,'executions':len(details)}),flush=True)
    return protected(folder/'baseline.json')


def current_target(entry):
    path=path_for(entry['name'])
    if path!=entry['id']:raise ValueError('Archive resource id differs')
    job=request('GET',path+API)
    if settings_invariant(job)!=entry['invariant'] or job['properties']['template']['containers'][0]['image']!=expected_image(entry['name']):
        raise ValueError('Target settings drift')
    runs=listing(path+'/executions','2026-07-01');assert_manual_idle(job,runs)
    if {x['name'] for x in runs}!={x['name'] for x in entry['executions']}:raise ValueError('Execution evidence changed')
    return job


def current_resources():
    return listing(BASE+'Microsoft.App/containerApps','2026-07-01')+listing(BASE+'Microsoft.App/jobs','2026-07-01')


def dependency_check():
    resources=current_resources();dependencies=[];aux=[]
    for r in resources:
        if r['name'] in APPROVED:continue
        hits=refs(r,APPROVED)
        for c in r['properties']['template']['containers']:
            argv=(c.get('command') or [])+(c.get('args') or [])
            if '-c' in argv and argv[-1]:
                for text in decode(argv[-1]):hits+=refs(text,APPROVED)
        if hits:dependencies.append({'caller':r['name'],'targets':sorted(set(hits)),'kind':'app_job'})
    types={}
    for r in az('resource','list','-g','rg-ibkr-database-v11'):types[r['type'].lower()]=types.get(r['type'].lower(),0)+1
    unsupported=[t for t in types if any(k in t for k in ('microsoft.logic/','microsoft.automation/','microsoft.web/sites','microsoft.datafactory/','microsoft.eventgrid/'))]
    if unsupported:dependencies.append({'kind':'unreviewed_scheduler','types':unsupported})
    for kind,version in (('Microsoft.Insights/actionGroups','2023-01-01'),('Microsoft.Insights/metricAlerts','2018-03-01'),('Microsoft.Insights/scheduledQueryRules','2023-12-01')):
        for r in listing(BASE+kind,version):
            hits=refs(r,APPROVED);aux.append({'id':r['id'],'sha256':hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest(),'targets':hits})
            if hits:dependencies.append({'kind':kind,'caller':r['name'],'targets':hits})
    for registry in ('ibkrv2stgauhecwc4itaoo','ibkrv115rcc62xxqblay'):
        for r in listing(rid('Microsoft.ContainerRegistry/registries',registry)+'/tasks','2019-06-01-preview'):
            hits=refs(r,APPROVED);aux.append({'id':r['id'],'sha256':hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest(),'targets':hits})
            if hits:dependencies.append({'kind':'registry_task','caller':r['name'],'targets':hits})
    return resources,dependencies,aux,types


def preserve_others(resources,plan):
    if bindings([r for r in resources if r['name'] not in APPROVED])!=plan['non_target_bindings']:
        raise ValueError('Non-target workload changed; re-review before retiring')


def main(stage):
    if stage=='capture':
        with ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(capture_one,sorted(APPROVED)))
        print('Seven exact definitions and execution records archived; nothing deleted');return
    entries=[protected(OUT/name/'baseline.json') for name in sorted(APPROVED)]
    if stage=='plan':
        if (OUT/'plan.json').exists():raise ValueError('Preserve frozen retirement plan')
        resources,dependencies,aux,types=dependency_check()
        for e in entries:current_target(e)
        local=[]
        for folder in ('src','scripts','infra'):
            for p in (ROOT/folder).rglob('*'):
                if p.suffix not in ('.py','.json','.bicep','.yaml','.yml'):continue
                hits=refs(p.read_text(encoding='utf-8',errors='replace'),APPROVED)
                if hits:local.append({'path':str(p.relative_to(ROOT)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'targets':hits})
        save(OUT,'plan',{'prepared_at':datetime.now(timezone.utc).isoformat(),'targets':sorted(APPROVED),
            'baseline_receipts':{e['name']:e['protected_receipt'] for e in entries},'non_target_bindings':bindings([r for r in resources if r['name'] not in APPROVED]),
            'dependencies':dependencies,'auxiliary_callers':aux,'resource_type_counts':types,'local_references':local,
            'owner_authorization':'Approve retirement after dependency checks','authorization_scope':'Six dated synthetic-runtime trials and inert log canary only, approved 10 October 2026',
            'plan_passed':not dependencies,'local_reference_review_required':True,'full_upgrade_complete':False})
        print(json.dumps({'dependencies':dependencies,'local_references':len(local),'plan_passed':not dependencies}));return
    plan=protected(OUT/'plan.json')
    if set(plan['targets'])!=APPROVED or plan['owner_authorization']!='Approve retirement after dependency checks' or plan['plan_passed'] is not True:
        raise ValueError('Exact authorized passing plan required')
    for e in entries:
        if e['protected_receipt']!=plan['baseline_receipts'][e['name']]:raise ValueError('Archive binding differs')
    qualified=protected(OUT/'local-reference-review.json')
    if qualified.get('passed') is not True or qualified['plan_receipt']!=plan['protected_receipt']:raise ValueError('Reviewed local callers required')
    controller_sha=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if stage=='prepare':
        resources,deps,aux,_=dependency_check();preserve_others(resources,plan)
        if deps or aux!=plan['auxiliary_callers']:raise ValueError('Caller changes require re-review')
        for e in entries:current_target(e)
        save(OUT,'ready',{'passed':True,'plan_receipt':plan['protected_receipt'],'review_receipt':qualified['protected_receipt'],
            'controller_sha256':controller_sha,'targets':sorted(APPROVED),
            'restore_warning':'Exact definitions are retained. Original Azure execution IDs cannot be recreated; archived ARM execution evidence and existing Log Analytics remain.'})
        print('Protected recovery/dependency gate ready; no deletion');return
    ready=protected(OUT/'ready.json')
    if ready['controller_sha256']!=controller_sha or ready['plan_receipt']!=plan['protected_receipt']:raise ValueError('Protected controller drift')
    if stage=='apply':
        runtime.safe_window();resources,deps,aux,_=dependency_check();preserve_others(resources,plan)
        if deps or aux!=plan['auxiliary_callers']:raise ValueError('Caller changed before deletion')
        for e in entries:current_target(e)
        if (OUT/'delete-batch-intent.json').exists():raise ValueError('Reconcile prior batch; never redispatch')
        save(OUT,'delete-batch-intent',{'started_at':datetime.now(timezone.utc).isoformat(),'ready_receipt':ready['protected_receipt'],'targets':sorted(APPROVED)})
        for e in entries:
            current_target(e);folder=OUT/e['name'];path=path_for(e['name'])
            save(folder,'delete-intent',{'id':path,'baseline_receipt':e['protected_receipt'],'ready_receipt':ready['protected_receipt']})
            ack=request('DELETE',path+API)
            save(folder,'delete-ack',{'id':path,'request_returned_success':True,'response_sha256':hashlib.sha256(json.dumps(ack,sort_keys=True).encode()).hexdigest()})
            print(json.dumps({'job':e['name'],'delete_submitted':True}),flush=True)
        return
    if stage!='verify':raise ValueError('Unknown stage')
    batch=protected(OUT/'delete-batch-intent.json')
    if batch['ready_receipt']!=ready['protected_receipt']:raise ValueError('Wrong deletion intent')
    remaining=[]
    for e in entries:
        path=path_for(e['name']);di=protected(OUT/e['name']/'delete-intent.json')
        if di['id']!=path:raise ValueError('Deletion path differs')
        if request('GET',path+API,missing=True) is not None:remaining.append(e['name'])
    preserve_others(current_resources(),plan)
    if remaining:print(json.dumps({'still_present':remaining}));return
    if not (OUT/'verified.json').exists():save(OUT,'verified',{'passed':True,'checked_at':datetime.now(timezone.utc).isoformat(),
        'retired_jobs':sorted(APPROVED),'retired_count':7,'archived_executions':sum(len(e['executions']) for e in entries),
        'all_other_app_job_settings_and_images_preserved':True,'plan_receipt':plan['protected_receipt'],'restore_receipt':ready['protected_receipt'],
        'identities_databases_reports_deleted':False,'full_upgrade_complete':False})
    print('Seven authorized diagnostic definitions retired; all other workloads preserved')


if __name__=='__main__':main(sys.argv[1])
