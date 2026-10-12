"""Dispatch/read isolated specialist rehearsals; committed trials clean up explicitly."""
import argparse
import base64
import copy
import gzip
import hashlib
import json
import re
from pathlib import Path
from audit_azure_runtime import az, RG
from deploy_metadata_monitor import request

ROOT=Path(__file__).resolve().parents[1]
JOB='ibkrv2stg-bootstrap'
IMAGE='sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['start','read'])
    parser.add_argument('--execution')
    parser.add_argument('--kind',choices=['audit','daily_delivery','report_builder','metadata_install','authority','authority_install','observation_reader'],default='audit')
    parser.add_argument('--apply',action='store_true',help='Explicitly install dormant metadata foundation only')
    args=parser.parse_args()
    if args.apply and (args.kind not in ('metadata_install','authority_install') or args.action!='start'):
        raise ValueError('Apply is only for the explicit metadata installation')
    if az('account','show','--query','id')!='3956e821-cee4-403f-8708-e84e2ed490eb':
        raise ValueError('Unexpected subscription')
    folder=ROOT/('.work/specialist-'+{'audit':'audit','daily_delivery':'delivery','report_builder':'report-builder','metadata_install':'metadata-install','authority':'authority','authority_install':'authority-install','observation_reader':'observation-reader'}[args.kind]+'-20261002')
    prefix={'audit':'SPECIALIST_AUDIT_TRIAL ','daily_delivery':'SPECIALIST_DELIVERY_TRIAL ',
            'report_builder':'SPECIALIST_REPORT_BUILDER_TRIAL ','metadata_install':'SPECIALIST_METADATA_INSTALL ','authority':'SPECIALIST_AUTHORITY_TRIAL ','authority_install':'SPECIALIST_AUTHORITY_INSTALL ','observation_reader':'SPECIALIST_OBSERVATION_READER_TRIAL '}[args.kind]
    folder.mkdir(parents=True,exist_ok=True)
    job=az('containerapp','job','show','-g',RG,'-n',JOB)
    settings_hash=hashlib.sha256(json.dumps({'identity':job.get('identity'),
        'configuration':job['properties']['configuration'],'template':job['properties']['template']},sort_keys=True).encode()).hexdigest()
    if args.action=='read':
        if not re.fullmatch(JOB+r'-[a-z0-9]{7}',args.execution or ''):
            raise ValueError('Exact execution required')
        state=az('containerapp','job','execution','show','-g',RG,'-n',JOB,'--job-execution-name',args.execution)
        logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
            "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+args.execution+"' | where Log_s startswith '"+prefix+"' | project Log_s")
        dispatch=json.loads((folder/(args.execution+'-dispatch.json')).read_text())
        result={'execution':args.execution,'status':state['properties']['status'],
            'job_settings_unchanged':dispatch['job_settings_sha256']==settings_hash,
            'records':[json.loads(r['Log_s'].removeprefix(prefix)) for r in logs]}
        if args.kind in ('authority','authority_install','observation_reader'):
            actual=state['properties']['template']['containers']
            if len(actual)!=1 or any(actual[0].get(k)!=dispatch['execution_container'][k] for k in ('image','command','args')):
                raise ValueError('Authority trial execution payload differs')
            env={e['name']:e for e in actual[0].get('env',[])}
            if set(env)!={'PORTFOLIO_TENANT_ID','DATABASE_URL'} or env['DATABASE_URL'].get('secretRef') not in ('database-admin-url','cappjob-'+JOB):
                raise ValueError('Authority trial credentials differ')
            if env['PORTFOLIO_TENANT_ID']!=next(e for e in dispatch['execution_container']['env'] if e['name']=='PORTFOLIO_TENANT_ID'):
                raise ValueError('Authority trial tenant differs')
            result.update(configuration_verified=True,source_sha256=dispatch['source_sha256'])
    else:
        config=job['properties']['configuration']
        if config['triggerType']!='Manual' or config.get('manualTriggerConfig',{}).get('parallelism')!=1:
            raise ValueError('Manual single-worker rehearsal required')
        executions=az('containerapp','job','execution','list','-g',RG,'-n',JOB)
        if any(e.get('properties',{}).get('status') in {'Running','Processing'} for e in executions):
            raise ValueError('Diagnostic job is already busy')
        template=copy.deepcopy(job['properties']['template'])
        if template.get('volumes') or len(template['containers'])!=1 or job['properties']['configuration']['replicaRetryLimit']!=0:
            raise ValueError('Unexpected diagnostic configuration')
        template.pop('volumes',None)
        container=template['containers'][0]
        if not container['image'].endswith('@'+IMAGE):
            raise ValueError('Diagnostic image changed')
        tenant=next(e for e in container['env'] if e['name']=='PORTFOLIO_TENANT_ID')
        container['env']=[tenant,{'name':'DATABASE_URL','secretRef':'database-admin-url'}]
        source="import sys,types\nsys.path.insert(0,'/app/src')\n"
        migrations=['056_specialist_audit_ledger.sql'] if args.kind=='audit' else ['057_specialist_daily_delivery.sql']
        if args.kind=='report_builder': migrations.insert(0,'056_specialist_audit_ledger.sql')
        if args.kind in ('authority','authority_install'): migrations=['058_specialist_observation_authority.sql']
        if args.kind=='observation_reader': migrations=[]
        source+='MIGRATION_SQL='+repr('\n'.join((ROOT/'migrations/postgres'/m).read_text() for m in migrations))+'\n'
        if args.kind=='metadata_install':
            source+='INSTALL_APPLY='+repr(args.apply)+'\n'
            source+='MIGRATIONS='+repr({m:(ROOT/'migrations/postgres'/m).read_text(encoding='utf-8') for m in
                ('056_specialist_audit_ledger.sql','057_specialist_daily_delivery.sql')})+'\n'
        if args.kind=='authority_install':
            source+='INSTALL_APPLY='+repr(args.apply)+'\n'
            source+='MIGRATIONS='+repr({m:(ROOT/'migrations/postgres'/m).read_text(encoding='utf-8') for m in migrations})+'\n'
        modules=('agent_governance','specialist_autonomy','specialist_audit') if args.kind=='audit' else ('agent_daily_schedule','agent_daily_outbox')
        if args.kind=='report_builder':
            modules=('agent_governance','specialist_autonomy','agent_daily_schedule','agent_daily_outbox','agent_daily_report','agent_daily_delivery','agent_daily_inbox','specialist_service_access')
        if args.kind=='metadata_install': modules=('specialist_service_access',)
        if args.kind=='authority_install': modules=()
        if args.kind=='observation_reader': modules=('specialist_observation_access',)
        if args.kind=='authority':
            modules=('agent_governance','specialist_autonomy','specialist_audit','specialist_audit_service',
                     'specialist_audit_runtime','agent_observation_audit','agent_observation','agent_authority')
        for name in modules:
            code=(ROOT/('src/ibkr_etl/'+name+'.py')).read_text()
            source+="m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl'\nsys.modules[m.__name__]=m\nexec(compile("+repr(code)+",'specialist-trial','exec'),m.__dict__)\n"
        verifier={'audit':'verify_specialist_audit.py','daily_delivery':'verify_specialist_delivery.py',
                  'report_builder':'verify_specialist_report_builder.py','metadata_install':'install_specialist_metadata.py','authority':'verify_specialist_authority.py','authority_install':'install_specialist_authority.py','observation_reader':'verify_specialist_observation_reader.py'}[args.kind]
        source+=(ROOT/'scripts'/verifier).read_text()
        encoded=base64.b64encode(source.encode()).decode()
        container['command']=['python']
        container['args']=['-c',"import base64;exec(compile(base64.b64decode('"+encoded+"'),'specialist-audit-trial','exec'))"]
        if args.kind in ('authority','authority_install','observation_reader'):
            encoded=base64.b64encode(gzip.compress(source.encode(),mtime=0)).decode()
            container['args']=['-c',"import base64,gzip;exec(compile(gzip.decompress(base64.b64decode('"+encoded+"')),'specialist-authority-trial','exec'))"]
        response=request('POST',job['id']+'/start?api-version=2024-03-01',data=template)
        mode = 'isolated_committed_schema_then_cleanup' if args.kind in ('report_builder','authority') else 'isolated_schema_force_rollback'
        if args.kind=='metadata_install': mode='install_dormant_metadata' if args.apply else 'inspect_metadata_only'
        if args.kind=='authority_install': mode='install_dormant_authority' if args.apply else 'inspect_authority_only'
        result={'execution':response['name'],'mode':mode,
            'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'job_settings_sha256':settings_hash}
        if args.kind in ('authority','authority_install','observation_reader'): result['execution_container']=container
    suffix='-dispatch' if args.action=='start' else ''
    (folder/(result['execution']+suffix+'.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='execution_container'}))


if __name__=='__main__':
    main()
