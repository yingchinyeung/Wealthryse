"""Inert, fixed-scope launch plan for the separately qualified archive canary.

No credential, SQL, network, discovery, deployment or start occurs here. Actual
native registrations, protected reservations and independent readback remain
mandatory. This archive-only qualification cannot activate an agent or Step 4.
"""
from __future__ import annotations

import base64
from dataclasses import asdict
import hashlib
import json
import lzma
import re
import uuid

import owner_task_worker_archive_trial as worker
from owner_task_provider_launch_contract import IMAGE, REGISTRY_SERVER

SUB='/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb'
RG=SUB+'/resourceGroups/rg-ibkr-database-v11'
ENVIRONMENT=worker.BASE+'Microsoft.App/managedEnvironments/ibkrv2stg-env'
REGISTRY=worker.BASE+'Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo'
DEPLOYMENT=RG+'/providers/Microsoft.Resources/deployments/ibkrv2stg-ownarchive-20261005'
VALIDATION=DEPLOYMENT+'-validation'
MODULES=(('src/ibkr_etl/util.py','ibkr_etl.util'),('src/ibkr_etl/db.py','ibkr_etl.db'),
    ('src/ibkr_etl/owner_codex_tasks.py','ibkr_etl.owner_codex_tasks'),
    ('src/ibkr_etl/owner_codex_worker.py','ibkr_etl.owner_codex_worker'),
    ('src/ibkr_etl/owner_codex_worker_audit.py','ibkr_etl.owner_codex_worker_audit'),
    (worker.PATH,'owner_task_worker_archive_trial'))
PATH='scripts/owner_task_worker_archive_launch.py'
CONTROL_PATH='scripts/deploy_owner_task_worker_archive_trial.py'
TEST_PATH='tests/test_owner_task_worker_archive_launch.py'
ADDITIONS=frozenset((PATH,CONTROL_PATH,TEST_PATH))
ROLES=dict(acr=(REGISTRY,'7f951dda-4ed3-4680-a7ca-43fe172d538d'),
    protection=(worker.ARM_SCOPE,'acdd72a7-3385-48ef-bd42-f606fba81ae7'),
    writer=(worker.ARM_SCOPE,'ba92f5b4-2d11-453d-a403-e96b0029c9fe'),
    verifier=(worker.ARM_SCOPE,'2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'))
IDENTITY_TAGS=dict(purpose='owner-worker-archive-qualification',operation=worker.OPERATION)
require=worker.require


def digest(value):return hashlib.sha256(worker.canonical(value)).hexdigest()


def assignment(mode,role):
    require(mode in worker.IDENTITIES and role in ('acr','protection',mode))
    scope,_=ROLES[role]
    name=str(uuid.uuid5(uuid.UUID(worker.OPERATION),mode+':'+role))
    return scope+'/providers/Microsoft.Authorization/roleAssignments/'+name


def identity_stack():
    """Exactly two NEW identities and six NEW grants, never group-wide access."""
    resources=[]
    for mode,resource in worker.IDENTITIES.items():
        resources.append(dict(type='Microsoft.ManagedIdentity/userAssignedIdentities',
            apiVersion='2023-01-31',name=resource.rsplit('/',1)[1],location='eastasia',
            tags=dict(IDENTITY_TAGS,mode=mode)))
        for role in ('acr','protection',mode):
            scope,definition=ROLES[role]
            resources.append(dict(type='Microsoft.Authorization/roleAssignments',apiVersion='2022-04-01',
                name=assignment(mode,role).rsplit('/',1)[1],scope=scope.removeprefix(RG+'/providers/'),dependsOn=[resource],
                properties=dict(principalId="[reference('"+resource+"', '2023-01-31', 'Full').properties.principalId]",
                    principalType='ServicePrincipal',roleDefinitionId=SUB+
                    '/providers/Microsoft.Authorization/roleDefinitions/'+definition)))
    return dict(properties=dict(mode='Incremental',template={
        '$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#',
        'contentVersion':'1.0.0.0','resources':resources}))


def registered(value,mode):
    require(mode in worker.IDENTITIES and type(value) is dict
        and value.get('id','').casefold()==worker.IDENTITIES[mode].casefold()
        and value.get('name')==worker.IDENTITIES[mode].rsplit('/',1)[1]
        and value.get('type')=='Microsoft.ManagedIdentity/userAssignedIdentities'
        and value.get('location')=='eastasia' and value.get('tags')==dict(IDENTITY_TAGS,mode=mode))
    p=value.get('properties')
    require(type(p) is dict and set(p)=={'tenantId','clientId','principalId'} and p['tenantId']==worker.TENANT)
    worker.guid(p['clientId']);worker.guid(p['principalId'])
    return p


def context(mode,identities,manifest):
    require(type(identities) is dict and set(identities)==set(worker.IDENTITIES))
    values={m:registered(identities[m],m) for m in identities}
    require(values['writer']['clientId']!=values['verifier']['clientId'])
    return worker.Context(mode,values[mode]['clientId'],values[mode]['principalId'],
        values['writer']['principalId'],values['verifier']['principalId'],manifest,IMAGE.split('@sha256:')[1])


BOOTSTRAP='''import hashlib,json,os,sys,types
assert sys.version_info[:3]==(3,12,14)
assert os.getuid()==os.getgid()==10001
assert _context['image_sha256']=='276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c'
assert set(_sources)==set(_hashes)==set(p for p,_ in _modules)
assert all(hashlib.sha256(s.encode()).hexdigest()==_hashes[p] for p,s in _sources.items())
package=types.ModuleType('ibkr_etl');package.__path__=[];sys.modules['ibkr_etl']=package
for path,name in _modules:
    module=types.ModuleType(name);module.__file__='/app/'+path
    module.__package__=name.rpartition('.')[0];sys.modules[name]=module
    exec(compile(_sources[path],module.__file__,'exec'),module.__dict__)
module=sys.modules['owner_task_worker_archive_trial']
result=module.main(module.Context(**_context),_references)
print(module.PREFIX+module.canonical(result).decode('ascii'),flush=True)
raise SystemExit(0 if result['status']=='synthetic_archive_boundary_verified' else 1)
'''


def build(mode,sources,hashes,ctx,references=None):
    require(mode in worker.JOBS and type(ctx) is worker.Context and ctx.mode==mode
        and ctx.image_sha256==IMAGE.split('@sha256:')[1]
        and set(sources)==set(hashes)=={p for p,_ in MODULES}
        and sum(len(s) for s in sources.values())<=256*1024
        and all(type(v) is bytes and hashlib.sha256(v).hexdigest()==hashes[k] for k,v in sources.items()))
    if mode=='writer':require(references is None)
    else:
        require(type(references) is dict and set(references)=={'normal','lost_ack_inspection'})
        for kind,key in (('normal','normal'),('lost_ack','lost_ack_inspection')):
            worker.reference(references[key],worker.payload(ctx,kind))
    decoded={p:s.decode('utf-8') for p,s in sources.items()}
    for path,source in decoded.items():
        require('\x00' not in source);compile(source,path,'exec')
    script=('_sources='+repr(decoded)+'\n_hashes='+repr(hashes)+'\n_modules='+repr(MODULES)+
        '\n_context='+repr(asdict(ctx))+'\n_references='+repr(references)+'\n'+BOOTSTRAP)
    packed=base64.b64encode(lzma.compress(script.encode(),preset=9)).decode()
    args=['-I','-B','-c',"import base64,lzma;exec(compile(lzma.decompress(base64.b64decode('"+
        packed+"')),'owner-archive-qualification','exec'))"]
    require(len(worker.canonical(args))<=65536 and b'$' not in worker.canonical(args))
    mi=worker.IDENTITIES[mode]
    definition=dict(location='eastasia',identity=dict(type='UserAssigned',userAssignedIdentities={mi:{}}),
        tags=dict(IDENTITY_TAGS,mode=mode,sourceSha256=digest(hashes),
            fullSourceSha256=hashlib.sha256(script.encode()).hexdigest()),
        properties=dict(environmentId=ENVIRONMENT,configuration=dict(triggerType='Manual',
            replicaTimeout=300,replicaRetryLimit=0,identitySettings=[dict(identity=mi,lifecycle='All')],
            manualTriggerConfig=dict(parallelism=1,replicaCompletionCount=1),secrets=[],
            registries=[dict(server=REGISTRY_SERVER,identity=mi)]),
            template=dict(containers=[dict(name='archive-'+mode,image=IMAGE,command=['python'],args=args,
                env=[],resources=dict(cpu=.5,memory='1Gi'))])))
    return dict(definition=definition,definition_sha256=digest(definition),context=asdict(ctx),references=references,
        source_hashes=hashes,full_script_sha256=hashlib.sha256(script.encode()).hexdigest())


def template(value,expected):
    v=json.loads(worker.canonical(value))
    for name in ('initContainers','volumes'):
        if name in v:require(v.pop(name) in (None,[]))
    require(type(v.get('containers')) is list and len(v['containers'])==1)
    c=v['containers'][0]
    if 'imageType' in c:require(c.pop('imageType')=='ContainerImage')
    if 'ephemeralStorage' in c.get('resources',{}):require(c['resources'].pop('ephemeralStorage')=='')
    require(v==expected)


def checked_job(value,plan,mode,identity):
    wanted=plan['definition'];require(plan['definition_sha256']==digest(wanted))
    require(value.get('id')==worker.JOBS[mode] and value.get('name')==worker.JOBS[mode].rsplit('/',1)[1]
        and value.get('type')=='Microsoft.App/jobs' and value.get('location') in ('eastasia','East Asia')
        and value.get('tags')==wanted['tags'])
    i=value.get('identity',{});assigned=i.get('userAssignedIdentities',{})
    require(i.get('type')=='UserAssigned' and set(i)<={'type','principalId','tenantId','userAssignedIdentities'}
        and i.get('principalId') is None and i.get('tenantId') is None and len(assigned)==1
        and next(iter(assigned)).casefold()==worker.IDENTITIES[mode].casefold()
        and next(iter(assigned.values()))=={k:identity[k] for k in ('clientId','principalId')})
    props=value.get('properties',{})
    require(props.get('environmentId')==ENVIRONMENT and props.get('provisioningState')=='Succeeded'
        and props.get('workloadProfileName') in (None,'Consumption')
        and set(props)<={'environmentId','configuration','template','provisioningState',
            'outboundIpAddresses','workloadProfileName','eventStreamEndpoint'})
    config=json.loads(worker.canonical(props.get('configuration')))
    for name in ('scheduleTriggerConfig','eventTriggerConfig','dapr'):
        if name in config:require(config.pop(name) is None)
    for field in ('identitySettings','registries'):
        require(len(config.get(field,[]))==1
            and config[field][0].get('identity','').casefold()==worker.IDENTITIES[mode].casefold())
        config[field][0]['identity']=worker.IDENTITIES[mode]
    for name in ('username','passwordSecretRef'):
        if name in config['registries'][0]:require(config['registries'][0].pop(name)=='')
    require(config==wanted['properties']['configuration'])
    template(props.get('template'),wanted['properties']['template'])
    return dict(native_job_sha256=digest(value),definition_sha256=plan['definition_sha256'])
