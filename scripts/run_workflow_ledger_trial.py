"""Dispatch/read an all-rollback PostgreSQL ledger rehearsal; never bootstrap."""
import argparse
import base64
import copy
import hashlib
import json
import re
from pathlib import Path
from audit_azure_runtime import az,RG
from deploy_metadata_monitor import request

ROOT=Path(__file__).resolve().parents[1]
JOB='ibkrv2stg-bootstrap'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','read'])
    parser.add_argument('--execution')
    args=parser.parse_args()
    if az('account','show','--query','id')!='3956e821-cee4-403f-8708-e84e2ed490eb':
        raise ValueError('Unexpected subscription')
    folder=ROOT/'.work/workflow-ledger-20261001'
    folder.mkdir(parents=True,exist_ok=True)
    job=az('containerapp','job','show','-g',RG,'-n',JOB)
    if args.action=='read':
        if not re.fullmatch(JOB+r'-[a-z0-9]{7}',args.execution or ''):
            raise ValueError('Exact execution required')
        state=az('containerapp','job','execution','show','-g',RG,'-n',JOB,'--job-execution-name',args.execution)
        logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb',
            '--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+
            "' | where Log_s startswith 'WORKFLOW_LEDGER_TRIAL ' | project Log_s")
        evidence={'execution':args.execution,'status':state['properties']['status'],
                  'records':[json.loads(r['Log_s'].removeprefix('WORKFLOW_LEDGER_TRIAL ')) for r in logs]}
        (folder/(args.execution+'.json')).write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        print(json.dumps(evidence))
        return
    sources={'SOURCE_POLICY':'src/ibkr_etl/workflow_readiness.py','SOURCE_LEDGER':'src/ibkr_etl/workflow_ledger.py',
             'MIGRATION_SQL':'migrations/postgres/054_shadow_workflow_evidence.sql'}
    script='\n'.join(f'{name}={(ROOT/path).read_text(encoding="utf-8")!r}' for name,path in sources.items())+'\n'
    script+=(ROOT/'scripts/verify_workflow_ledger.py').read_text(encoding='utf-8')
    template=copy.deepcopy(job['properties']['template'])
    if template.get('volumes') or len(template['containers'])!=1 or job['properties']['configuration']['replicaRetryLimit']!=0:
        raise ValueError('Unexpected diagnostic configuration')
    template.pop('volumes',None)
    container=template['containers'][0]
    tenant=next(e for e in container['env'] if e['name']=='PORTFOLIO_TENANT_ID')
    container['env']=[tenant,{'name':'DATABASE_URL','secretRef':'database-admin-url'}]
    container['command']=['python']
    encoded=base64.b64encode(script.encode()).decode()
    container['args']=['-c',f"import base64;exec(compile(base64.b64decode('{encoded}'),'workflow-ledger-trial','exec'))"]
    result=request('POST',job['id']+'/start?api-version=2024-03-01',data=template)
    record={'execution':result['name'],'mode':'rollback_only','source_sha256':hashlib.sha256(script.encode()).hexdigest()}
    (folder/(result['name']+'-dispatch.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record))


if __name__=='__main__':
    main()
