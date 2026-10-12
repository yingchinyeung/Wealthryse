"""Dispatch/retrieve the fixed-scope crisis repair; no persistent job changes."""
import argparse
import base64
import copy
import hashlib
import json
import re
from pathlib import Path
from audit_azure_runtime import az,RG
from deploy_metadata_monitor import request
from pin_risk_worker_release import IMAGE

ROOT=Path(__file__).resolve().parents[1]
JOB='ibkrv2stg-risk-snapshots'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['preview','apply','verify','read'])
    parser.add_argument('--expected-sha256',default='')
    parser.add_argument('--execution')
    args=parser.parse_args()
    if az('account','show','--query','id')!='3956e821-cee4-403f-8708-e84e2ed490eb':
        raise ValueError('Unexpected subscription')
    folder=ROOT/'.work'/'crisis-repair-20261001'
    folder.mkdir(parents=True,exist_ok=True)
    job=az('containerapp','job','show','-g',RG,'-n',JOB)
    if args.mode=='read':
        if not re.fullmatch(JOB+r'-[a-z0-9]{7}',args.execution or ''):
            raise ValueError('Exact execution required')
        state=az('containerapp','job','execution','show','-g',RG,'-n',JOB,'--job-execution-name',args.execution)
        logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb',
            '--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+
            "' | where Log_s startswith 'CRISIS_REPAIR ' | project Log_s")
        records=[json.loads(row['Log_s'].removeprefix('CRISIS_REPAIR ')) for row in logs]
        evidence={'execution':args.execution,'status':state['properties']['status'],'records':records}
        (folder/(args.execution+'.json')).write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        print(json.dumps(evidence))
        return
    if args.mode=='apply' and not re.fullmatch('[0-9a-f]{64}',args.expected_sha256):
        raise ValueError('Reviewed preview input hash required')
    template=copy.deepcopy(job['properties']['template'])
    if template.get('volumes') or len(template['containers'])!=1 or job['properties']['configuration']['replicaRetryLimit']!=0:
        raise ValueError('Unexpected job configuration')
    if template['containers'][0]['image']!=IMAGE:
        raise ValueError('Unreviewed runtime image')
    template.pop('volumes',None)
    helper=(ROOT/'scripts/inspect_crisis_gap.py').read_text(encoding='utf-8')
    source='import sys,types\nhelper=types.ModuleType("inspect_crisis_gap")\nsys.modules[helper.__name__]=helper\n'
    source+='exec(compile('+repr(helper)+',"crisis-preflight","exec"),helper.__dict__)\n'
    source+='REPAIR_MODE='+repr(args.mode)+'\nEXPECTED_INPUT_SHA256='+repr(args.expected_sha256)+'\n'
    source+=(ROOT/'scripts/repair_sampled_crisis_replay.py').read_text(encoding='utf-8')
    encoded=base64.b64encode(source.encode()).decode()
    template['containers'][0]['command']=['python']
    template['containers'][0]['args']=['-c',f"import base64;exec(compile(base64.b64decode('{encoded}'),'scoped-crisis-repair','exec'))"]
    result=request('POST',job['id']+'/start?api-version=2024-03-01',data=template)
    record={'execution':result['name'],'mode':args.mode,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'image':IMAGE,'expected_sha256':args.expected_sha256}
    (folder/(result['name']+'-dispatch.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record))


if __name__=='__main__':
    main()
