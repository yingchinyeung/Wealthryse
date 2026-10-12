"""Exact-scope archive reader grants and read-only manual execution override.

Leaves the installed, previously verified access-check definition unchanged.
No admin credentials, storage writes, schedule activation or email capability.
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
from deploy_metadata_monitor import request, SUB
from deploy_specialist_report_worker import ROOT, JOB, IDENTITY_PATH, job_path, validate_job
from ibkr_etl.specialist_archive_reader import SCOPE, CLIENT_ID

FOLDER=ROOT/'.work/specialist-report-preview-20261002'
PRINCIPAL='a887611d-6b63-45c4-9b94-03c42405367c'
ROLES=('2a2b9908-6ea1-4ae2-8e65-a410df84e7d1','acdd72a7-3385-48ef-bd42-f606fba81ae7')
PREFIX='SPECIALIST_REPORT_PREVIEW '


def bundle():
    source="import sys,types\nsys.path.insert(0,'/app/src')\n"
    for name in ('agent_governance','specialist_autonomy','agent_daily_schedule','agent_daily_outbox',
                 'agent_daily_report','specialist_report_runtime','specialist_archive_reader',
                 'specialist_audit','specialist_audit_service','specialist_audit_runtime',
                 'agent_observation_evidence','agent_observation_audit','specialist_evidence'):
        code=(ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8')
        source+="m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'report-preview','exec'),m.__dict__)\n"
    source+=(ROOT/'scripts/preview_specialist_report.py').read_text(encoding='utf-8')
    encoded=base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    return ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'specialist-report-preview','exec'))"],hashlib.sha256(source.encode()).hexdigest()


def settings_hash(job):
    return hashlib.sha256(json.dumps({k:job[k] for k in ('identity','properties')},sort_keys=True).encode()).hexdigest()


def verify_grant(properties,role):
    if (properties.get('principalId')!=PRINCIPAL or properties.get('scope','').casefold()!=SCOPE.casefold()
            or properties.get('roleDefinitionId','').casefold()!=f'/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{role}'.casefold()
            or properties.get('condition')):
        raise ValueError('Unexpected reporting archive grant')


def grants(apply=False):
    identity=request('GET',IDENTITY_PATH+'?api-version=2023-01-31')
    if identity['properties']['principalId']!=PRINCIPAL or identity['properties']['clientId']!=CLIENT_ID:
        raise ValueError('Reporting identity changed')
    for role in ROLES:
        assignment=str(uuid.uuid5(uuid.NAMESPACE_URL,IDENTITY_PATH+SCOPE+role))
        path=SCOPE+'/providers/Microsoft.Authorization/roleAssignments/'+assignment+'?api-version=2022-04-01'
        existing=request('GET',path,missing=True)
        if not existing:
            if not apply: raise ValueError('Archive reader grant not installed')
            request('PUT',path,data={'properties':{'principalId':PRINCIPAL,'principalType':'ServicePrincipal',
                    'roleDefinitionId':f'/subscriptions/{SUB}/providers/Microsoft.Authorization/roleDefinitions/{role}'}})
            existing=request('GET',path)
        verify_grant(existing['properties'],role)


def expected_template(job):
    validate_job(job)
    template={'containers':copy.deepcopy(job['properties']['template']['containers'])}
    # ARM GET includes empty read-only/default fields that are not execution
    # overrides. Submit only the reviewed writable resource values.
    template['containers'][0]['resources']={'cpu':0.5,'memory':'1Gi'}
    template['containers'][0]['args']=bundle()[0]
    return template


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=['grant','start','read'])
    parser.add_argument('--execution')
    args=parser.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01')
    validate_job(job)
    grants(apply=args.stage=='grant')
    if args.stage=='grant':
        print(json.dumps({'archive_reader_grants_verified':True,'scope':SCOPE,'principal':PRINCIPAL,
                          'storage_write_granted':False,'raw_storage_access_granted':False}))
        return
    FOLDER.mkdir(parents=True,exist_ok=True)
    template=expected_template(job)
    if args.stage=='start':
        runs=request('GET',job_path(JOB)+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] in ('Running','Processing') for r in runs):
            raise ValueError('Reporting worker busy')
        result=request('POST',job_path(JOB)+'/start?api-version=2024-03-01',data=template)
        dispatch={'execution':result['name'],'mode':'read_only_preview','payload_sha256':bundle()[1],
                  'job_settings_sha256':settings_hash(job)}
        (FOLDER/(result['name']+'-dispatch.json')).write_text(json.dumps(dispatch,indent=2),encoding='utf-8')
        print(json.dumps(dispatch))
        return
    if not re.fullmatch(re.escape(JOB)+r'-[a-z0-9]{7}',args.execution or ''):
        raise ValueError('Exact execution required')
    dispatch=json.loads((FOLDER/(args.execution+'-dispatch.json')).read_text())
    run=request('GET',job_path(JOB)+'/executions/'+args.execution+'?api-version=2024-03-01')
    actual=run['properties']['template']['containers']
    wanted=template['containers'][0]
    if len(actual)!=1 or any(actual[0].get(k)!=wanted[k] for k in ('image','command','args')):
        raise ValueError('Executed report preview differs from reviewed payload')
    if any(actual[0].get('resources',{}).get(k)!=v for k,v in wanted['resources'].items()):
        raise ValueError('Executed report preview resources differ')
    env=actual[0].get('env',[])
    if len(env)!=1 or env[0].get('name')!='SPECIALIST_REPORT_DATABASE_URL' or env[0].get('secretRef') not in ('report-url','cappjob-'+JOB):
        raise ValueError('Executed reporting credential differs')
    if dispatch['job_settings_sha256']!=settings_hash(job) or dispatch['payload_sha256']!=bundle()[1]:
        raise ValueError('Installed worker settings or preview source changed')
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    records=[json.loads(r['Log_s'].removeprefix(PREFIX)) for r in logs]
    result={'execution':args.execution,'status':run['properties']['status'],'payload_sha256':bundle()[1],
            'configuration_verified':True,'job_settings_unchanged':True,'records':records}
    (FOLDER/(args.execution+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))
    if result['status']!='Succeeded' or len(records)!=1 or records[0].get('status')!='passed':
        raise ValueError('Successful read-only report evidence not yet available')


if __name__=='__main__': main()
