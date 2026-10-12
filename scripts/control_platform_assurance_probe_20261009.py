"""One-off diagnostic execution; never modify saved job definitions."""
import hashlib,json,sys
from pathlib import Path
from deploy_metadata_monitor import request,BASE
from deploy_t_runtime_release_20261009 import idle
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from audit_azure_runtime import az
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'scripts/probe_platform_assurance_inputs_20261009.py'
OUT=ROOT/'.work/platform-corrections-20261009-v1/assurance-probe'
JOB='ibkrv2stg-onboard-render';PATH=BASE+'Microsoft.App/jobs/'+JOB;API='?api-version=2024-03-01'
def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump(value,f,indent=2)
def main(stage):
    if stage=='start':
        if OUT.exists():raise ValueError('Existing probe; inspect, never redispatch')
        idle(JOB);job=request('GET',PATH+API)
        image=request('GET',BASE+'Microsoft.App/containerApps/ibkrv2stg-web'+API)['properties']['template']['containers'][0]['image']
        source=SOURCE.read_text(encoding='utf-8')
        template=corrected_template(job,source);container=template['containers'][0];container['image']=image
        keep={'DATABASE_URL','PORTFOLIO_TENANT_ID','AZURE_CLIENT_ID'}
        container['env']=[e for e in container.get('env',[]) if e['name'] in keep]+[{'name':'AUTHORIZATION_MODE','value':'database'}]
        OUT.mkdir()
        save('intent',{'template':template,'job_invariant':settings_invariant(job),'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'database_writes':0,'model_calls':0})
        ack=request('POST',PATH+'/start'+API,data=template)
        save('dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=json.loads((OUT/'intent.json').read_bytes());dispatch=json.loads((OUT/'dispatch.json').read_bytes())
    if settings_invariant(request('GET',PATH+API))!=intent['job_invariant']:raise ValueError('Saved job changed')
    execution=dispatch['execution'];run=request('GET',PATH+'/executions/'+execution+API)
    print(json.dumps({'execution':execution,'status':run['properties']['status']}))
    if run['properties']['status'] not in ('Succeeded','Failed'):return
    actual=run['properties']['template']['containers'][0];expected=intent['template']['containers'][0]
    if any(actual.get(k)!=expected.get(k) for k in ('image','command','args')):raise ValueError('Executed source differs')
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith 'PLATFORM_CORRECTION_PROBE ' | project TimeGenerated,Result=parse_json(substring(Log_s,26))"
    result=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if result:
        save('result',{'execution':execution,'status':run['properties']['status'],'records':result,'job_settings_preserved':True})
    print(json.dumps(result,indent=2))
if __name__=='__main__':main(sys.argv[1])
