"""Installed onboarding image, normal restricted-role SELECT-only postflight."""
import hashlib,json,sys
from pathlib import Path
import deploy_reporting_onboard_20261011 as d
from read_reporting_web_installed_receipt_20261011 import validate_env
b=d.b;OUT=d.qa.STAGE/'onboard-installed-role-v1'

def sources():return {n:hashlib.sha256((d.qa.ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'probe_reporting_assurance_installed_20261011.py','read_reporting_web_installed_receipt_20261011.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Exact postflight only')
    verified=d.protected(d.OUT/'verified.json');plan=d.protected(d.OUT/'plan.json');image=plan['image']
    source=d.release.r.read(d.release.OUT/'source-intent.json');job=b.request('GET',d.PATH+b.API);d.scope(job)
    if not verified['passed'] or verified['image']!=image or job['properties']['template']['containers'][0]['image']!=image or d.settings_invariant(job)!=plan['invariant']:raise ValueError('Installed onboarding worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve previous execution')
        b.runtime.safe_window();b.runtime.idle(d.JOB);d.only_target(b.bindings(b.current_resources()),plan['bindings'])
        probe=(d.qa.ROOT/'scripts/probe_reporting_assurance_installed_20261011.py').read_text()
        if probe.count('REPORTING_ASSURANCE_INSTALLED_')!=2:raise ValueError('Exact receipt markers required')
        code='import sys\nsys.path[:0]=["/app/src","/app/scripts"]\nSOURCE_HASHES='+repr(source['candidate_hashes'])+'\nIMAGE='+repr(image)+'\n'+probe.replace('REPORTING_ASSURANCE_INSTALLED_','REPORTING_ONBOARD_INSTALLED_')
        template=b.corrected_template(job,code);container=template['containers'][0]
        container['env']=[e for e in container.get('env',[]) if e['name'] in ('DATABASE_URL','PORTFOLIO_TENANT_ID')]
        validate_env(d.JOB,container['env'],container['env'])
        d.save(OUT,'intent',{'template':template,'sources':sources(),'image':image,'scope':'Five SELECT-only sector requests with normal onboarding worker role and exact installed-source checks. No onboarding workflow, report registration, storage write, approval or saved-job changes.'})
        ack=b.request('POST',d.PATH+'/start'+b.API,data=template);d.save(OUT,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=d.protected(OUT/'intent.json')
    if intent['sources']!=sources():raise ValueError('Postflight source changed')
    name=d.protected(OUT/'dispatch.json')['execution'];run=b.request('GET',d.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Executed image or command differs')
    validate_env(d.JOB,actual.get('env',[]),wanted['env']);status=run['properties']['status']
    if status not in ('Succeeded','Failed'):print(status);return
    prefix='REPORTING_ONBOARD_INSTALLED_FINAL ' if status=='Succeeded' else 'REPORTING_ONBOARD_INSTALLED_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting proof');return
    proof=json.loads(rows[0]['Result']);passed=status=='Succeeded'
    if passed and (not proof['passed'] or proof['image']!=image or proof['source_hashes']!=source['candidate_hashes'] or len(proof['subjects'])!=5 or {r['subject'] for r in proof['subjects']}!=set(d.qa.SUBJECTS) or not all(r['passed'] for r in proof['subjects']) or proof['database_writes']!=0 or proof['privileged_role'] is not False or not proof['database_readonly'] or not proof['verified_tls']):raise ValueError('Incomplete normal-role acceptance')
    label='result' if passed else 'failure'
    if not (OUT/(label+'.json')).exists():d.save(OUT,label,{'passed':passed,'execution':name,'image':image,'proof':proof,'saved_job_unchanged':True,'normal_onboarding_workflow_executed':False,'real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED'})
    print(json.dumps({'passed':passed,'proof':proof}))

if __name__=='__main__':main(*sys.argv[1:])
