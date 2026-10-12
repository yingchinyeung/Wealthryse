"""Dispatch independent, read-only evidence acceptance using the reporting identity."""
import argparse
import base64
import copy
import gzip
import hashlib
import json
import re
from audit_azure_runtime import az
from deploy_metadata_monitor import request,SUB
from deploy_specialist_report_worker import ROOT,JOB,job_path,validate_job
from run_specialist_capture import FOLDER as CAPTURE_FOLDER

FOLDER=ROOT/'.work/specialist-capture-reader-20261002'


def source_for(capture_execution):
    if not re.fullmatch('ibkrv2stg-agent-audit-[a-z0-9]{7}',capture_execution): raise ValueError('Exact capture execution required')
    record=json.loads((CAPTURE_FOLDER/(capture_execution+'.json')).read_text())
    dispatch=json.loads((CAPTURE_FOLDER/(capture_execution+'-dispatch.json')).read_text())
    if (record['status']!='Succeeded' or record.get('configuration_verified') is not True
        or record.get('job_settings_unchanged') is not True or len(record['records'])!=1
        or record['records'][0].get('status')!='passed' or record['records'][0].get('operation_id')!=dispatch['operation_id']
        or record['payload_sha256']!=dispatch['payload_sha256']): raise ValueError('Verified capture receipt required')
    source="import sys,types\nsys.path.insert(0,'/app/src')\n"
    source+='CAPTURE_OPERATION_ID='+repr(dispatch['operation_id'])+'\nCAPTURE_SOURCE_MANIFEST_SHA256='+repr(dispatch['source_manifest_sha256'])+'\n'
    for name in ('specialist_report_runtime','specialist_archive_reader','specialist_capture_verifier'):
        code=(ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8')
        source+="m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'capture-reader','exec'),m.__dict__)\n"
    source+=(ROOT/'scripts/read_specialist_capture.py').read_text(encoding='utf-8')
    encoded=base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
    return ['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'capture-reader','exec'))"],hashlib.sha256(source.encode()).hexdigest(),dispatch['operation_id']


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['start','read'])
    parser.add_argument('--capture-execution',required=True);parser.add_argument('--execution');args=parser.parse_args()
    if az('account','show','--query','id')!=SUB: raise ValueError('Unexpected subscription')
    job=request('GET',job_path(JOB)+'?api-version=2024-03-01');validate_job(job)
    payload,digest,operation=source_for(args.capture_execution)
    template={'containers':copy.deepcopy(job['properties']['template']['containers'])}
    template['containers'][0]['args']=payload;template['containers'][0]['resources']={'cpu':0.5,'memory':'1Gi'}
    FOLDER.mkdir(parents=True,exist_ok=True)
    if args.stage=='start':
        runs=request('GET',job_path(JOB)+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] not in ('Succeeded','Failed','Stopped') for r in runs): raise ValueError('Reporting worker busy')
        response=request('POST',job_path(JOB)+'/start?api-version=2024-03-01',data=template)
        result={'execution':response['name'],'capture_execution':args.capture_execution,'payload_sha256':digest,'operation_id':operation}
        (FOLDER/(response['name']+'-dispatch.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result));return
    if not re.fullmatch(re.escape(JOB)+r'-[a-z0-9]{7}',args.execution or ''): raise ValueError('Exact reader execution required')
    dispatch=json.loads((FOLDER/(args.execution+'-dispatch.json')).read_text())
    if dispatch['payload_sha256']!=digest or dispatch['capture_execution']!=args.capture_execution: raise ValueError('Reader dispatch changed')
    run=request('GET',job_path(JOB)+'/executions/'+args.execution+'?api-version=2024-03-01')
    actual=run['properties'].get('template',{}).get('containers',[]);wanted=template['containers'][0]
    if len(actual)!=1 or any(actual[0].get(k)!=wanted[k] for k in ('image','command','args')): raise ValueError('Reader execution payload differs')
    env=actual[0].get('env',[])
    if len(env)!=1 or env[0].get('name')!='SPECIALIST_REPORT_DATABASE_URL' or env[0].get('secretRef') not in ('report-url','cappjob-'+JOB): raise ValueError('Reader credential differs')
    logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith 'SPECIALIST_CAPTURE_READER ' | project Log_s")
    records=[json.loads(r['Log_s'].removeprefix('SPECIALIST_CAPTURE_READER ')) for r in logs]
    result={'execution':args.execution,'status':run['properties']['status'],'payload_sha256':digest,'configuration_verified':True,
            'job_settings_unchanged':True,'records':records}
    (FOLDER/(args.execution+'.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result))
    if result['status']!='Succeeded' or len(records)!=1 or records[0].get('status')!='passed' or records[0].get('operation_id')!=operation:
        raise ValueError('Independent capture readback incomplete')


if __name__=='__main__':main()
