"""Scoped protected capture rehearsal; preserves the installed manual job.

Durable local dispatch intent precedes launch. Explicit reuse of an operation
is required after an ambiguous/failed start; no automatic immediate retries.
"""
import argparse
import base64
import copy
import gzip
import hashlib
import json
import re
import uuid

from audit_azure_runtime import az
from deploy_metadata_monitor import request,SUB
from deploy_specialist_audit_worker import ROOT,JOB,IDENTITY_PATH,job_path,validate,idle
from ibkr_etl.specialist_capture import CLIENT_ID,PRINCIPAL_ID
from ibkr_etl.specialist_archive_reader import SCOPE

FOLDER=ROOT/'.work/specialist-audit-capture-20261002'
ROLES=('ba92f5b4-2d11-453d-a403-e96b0029c9fe','acdd72a7-3385-48ef-bd42-f606fba81ae7')
MODULES=('agent_governance','specialist_autonomy','specialist_audit','specialist_audit_service',
         'specialist_audit_runtime','specialist_archive_reader','specialist_capture')


def bundle(operation,recovery_rehearsal=False):
    if str(uuid.UUID(operation))!=operation: raise ValueError('Exact operation UUID required')
    files={'src/ibkr_etl/'+name+'.py':(ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8') for name in MODULES}
    runner='scripts/capture_specialist_recovery_check.py' if recovery_rehearsal else 'scripts/capture_specialist_permission_check.py'
    files[runner]=(ROOT/runner).read_text(encoding='utf-8')
    manifest={path:hashlib.sha256(code.encode()).hexdigest() for path,code in files.items()}
    manifest_hash=hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    source="import sys,types\nsys.path.insert(0,'/app/src')\n"
    source+='CAPTURE_OPERATION_ID='+repr(operation)+'\nCAPTURE_SOURCE_MANIFEST_SHA256='+repr(manifest_hash)+'\n'
    for name in MODULES:
        code=files['src/ibkr_etl/'+name+'.py']
        source+="m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'capture-runtime','exec'),m.__dict__)\n"
    source+=files[runner]
    encoded=base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    return ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'specialist-capture','exec'))"],hashlib.sha256(source.encode()).hexdigest(),manifest_hash


def settings_hash(job):
    value={'identity':job['identity'],'environmentId':job['properties']['environmentId'],
           'configuration':job['properties']['configuration'],'template':job['properties']['template']}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def verify_grant(p,role):
    if (p.get('principalId')!=PRINCIPAL_ID or p.get('scope','').casefold()!=SCOPE.casefold()
        or p.get('roleDefinitionId','').casefold()!=f'/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{role}'.casefold()
        or p.get('condition')): raise ValueError('Audit capture grant scope differs')


def grants(apply=False):
    identity=request('GET',IDENTITY_PATH+'?api-version=2023-01-31')['properties']
    if identity['principalId']!=PRINCIPAL_ID or identity['clientId']!=CLIENT_ID:
        raise ValueError('Unexpected capture identity')
    for role in ROLES:
        assignment=str(uuid.uuid5(uuid.NAMESPACE_URL,IDENTITY_PATH+SCOPE+role))
        path=SCOPE+'/providers/Microsoft.Authorization/roleAssignments/'+assignment+'?api-version=2022-04-01'
        existing=request('GET',path,missing=True)
        if not existing:
            if not apply: raise ValueError('Protected capture grant missing')
            request('PUT',path,data={'properties':{'principalId':PRINCIPAL_ID,'principalType':'ServicePrincipal',
                'roleDefinitionId':f'/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{role}'}})
            existing=request('GET',path)
        verify_grant(existing['properties'],role)


def template(job,operation,recovery_rehearsal=False):
    validate(job)
    value={'containers':copy.deepcopy(job['properties']['template']['containers'])}
    value['containers'][0]['args']=bundle(operation,recovery_rehearsal)[0]
    value['containers'][0]['resources']={'cpu':0.5,'memory':'1Gi'}
    return value


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['grant','start','read'])
    parser.add_argument('--execution')
    parser.add_argument('--operation',help='Explicit recovery of an existing local dispatch intent only')
    parser.add_argument('--recovery-rehearsal',action='store_true',help='Stop the first execution after committed intent; explicitly resume the same operation')
    args=parser.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01');validate(job)
    grants(apply=args.stage=='grant')
    if args.stage=='grant':
        print(json.dumps({'protected_capture_grants_verified':True,'scope':SCOPE,'principal_id':PRINCIPAL_ID,
                          'raw_storage_access_granted':False,'can_change_legal_hold':False}));return
    FOLDER.mkdir(parents=True,exist_ok=True)
    if args.stage=='start':
        idle(JOB)
        operation=args.operation or str(uuid.uuid4())
        _,digest,manifest=bundle(operation,args.recovery_rehearsal)
        intent={'operation_id':operation,'payload_sha256':digest,'source_manifest_sha256':manifest,
                'job_settings_sha256':settings_hash(job),'mode':'trusted_manual_permission_inspection',
                **({'recovery_rehearsal':True} if args.recovery_rehearsal else {})}
        path=FOLDER/(operation+'-intent.json')
        if args.operation:
            if not path.exists() or json.loads(path.read_text())!=intent:
                raise ValueError('Recovery must preserve registered operation, source and scope')
        else:
            with path.open('x',encoding='utf-8') as output: json.dump(intent,output,indent=2)
        print(json.dumps({'dispatch_intent_registered':True,**intent}),flush=True)
        response=request('POST',job_path(JOB)+'/start?api-version=2024-03-01',data=template(job,operation,args.recovery_rehearsal))
        dispatch={'execution':response['name'],**intent}
        (FOLDER/(response['name']+'-dispatch.json')).write_text(json.dumps(dispatch,indent=2),encoding='utf-8')
        print(json.dumps(dispatch));return
    if not re.fullmatch(re.escape(JOB)+r'-[a-z0-9]{7}',args.execution or ''):
        raise ValueError('Exact execution required')
    dispatch=json.loads((FOLDER/(args.execution+'-dispatch.json')).read_text())
    operation=dispatch['operation_id']
    rehearsal=dispatch.get('recovery_rehearsal',False)
    wanted=template(job,operation,rehearsal)['containers'][0]
    run=request('GET',job_path(JOB)+'/executions/'+args.execution+'?api-version=2024-03-01')
    actual=run['properties'].get('template',{}).get('containers',[])
    if len(actual)!=1 or any(actual[0].get(k)!=wanted[k] for k in ('image','command','args')):
        raise ValueError('Capture execution differs from reviewed payload')
    env=actual[0].get('env',[])
    if len(env)!=1 or env[0].get('name')!='SPECIALIST_AUDIT_DATABASE_URL' or env[0].get('secretRef') not in ('audit-url','cappjob-'+JOB):
        raise ValueError('Capture credential bindings differ')
    if settings_hash(job)!=dispatch['job_settings_sha256'] or bundle(operation,rehearsal)[1]!=dispatch['payload_sha256']:
        raise ValueError('Capture source or installed job changed')
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith 'SPECIALIST_AUDIT_CAPTURE ' | project Log_s")
    records=[json.loads(r['Log_s'].removeprefix('SPECIALIST_AUDIT_CAPTURE ')) for r in logs]
    result={'execution':args.execution,'status':run['properties']['status'],'payload_sha256':dispatch['payload_sha256'],
            'configuration_verified':True,'job_settings_unchanged':True,'records':records}
    (FOLDER/(args.execution+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    if result['status']!='Succeeded' or len(records)!=1 or records[0].get('status')!='passed' or records[0].get('operation_id')!=operation:
        raise ValueError('Verified capture incomplete; retain the operation ID for recovery')


if __name__=='__main__': main()
