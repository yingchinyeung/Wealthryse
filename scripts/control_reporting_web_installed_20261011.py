"""Actual website-role post-deployment check; no saved-job or data writes."""
import hashlib,json,sys
from pathlib import Path
import deploy_reporting_sector_web_20261011 as w
from control_upgrade_readers_20261010 import execution_env

b=w.b;OUT=w.STAGE/'web-installed-role-v1';JOB='ibkrv2stg-inbox-acceptance-r2';PATH=b.rid('Microsoft.App/jobs',JOB)
def sources():return {n:hashlib.sha256((w.ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'probe_reporting_web_installed_20261011.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Exact installed read-only trial only')
    image,source,_=w.candidate('web');verified=w.protected(w.OUT/'verified.json')
    if not verified['passed'] or verified['image']!=image:raise ValueError('Verified exact website image required')
    web=b.request('GET',w.PATH+w.API)
    if web['properties']['template']['containers'][0]['image']!=image:raise ValueError('Website image drift')
    baseline=w.protected(w.STAGE/'web-boundary-v1/result.json')['boundary']['workloads']
    job=b.request('GET',PATH+b.API)
    if b.bindings([job])[JOB]!=baseline[JOB]:raise ValueError('Saved inbox job drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve prior runtime')
        b.runtime.safe_window();b.runtime.idle(JOB)
        if b.bindings(b.current_resources())!=baseline:raise ValueError('Workload drift')
        code='import sys\nsys.path[:0]=["/app/src","/app/scripts"]\nSOURCE_HASHES='+repr(source['candidate_hashes'])+'\nIMAGE='+repr(image)+'\n'+(w.ROOT/'scripts/probe_reporting_web_installed_20261011.py').read_text()
        template=b.corrected_template(job,code);container=template['containers'][0];container['image']=image
        container['env']=[e for e in container.get('env',[]) if e['name'] in ('DATABASE_URL','PORTFOLIO_TENANT_ID')]
        references=[e for e in container['env'] if e['name']=='DATABASE_URL']
        if len(references)!=1 or not references[0].get('secretRef') or references[0].get('value'):raise ValueError('Existing website-role reference only')
        w.save(OUT,'intent',{'template':template,'sources':sources(),'image':image,'scope':'14 synthetic authorization tests; five actual SELECT-only sector requests under ibkr_stg_web with verified TLS. No reports, approvals, model calls or saved-job changes.'})
        ack=b.request('POST',PATH+'/start'+b.API,data=template);w.save(OUT,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=w.protected(OUT/'intent.json')
    if intent['sources']!=sources():raise ValueError('Runtime source drift')
    execution=w.protected(OUT/'dispatch.json')['execution'];run=b.request('GET',PATH+'/executions/'+execution+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Executed template differs')
    execution_env(JOB,actual.get('env',[]),wanted['env']);status=run['properties']['status']
    if status not in ('Succeeded','Failed'):print(status);return
    prefix='REPORTING_WEB_INSTALLED_FINAL ' if status=='Succeeded' else 'REPORTING_WEB_INSTALLED_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting proof');return
    proof=json.loads(rows[0]['Result']);passed=status=='Succeeded'
    if passed and (not proof['passed'] or proof['image']!=image or proof['source_hashes']!=source['candidate_hashes'] or len(proof['subjects'])!=5 or proof['synthetic_authorization_tests']!=14 or proof['database_writes']!=0 or proof['database_role']!='ibkr_stg_web' or not proof['database_readonly'] or not proof['verified_tls']):raise ValueError('Incomplete installed-role proof')
    label='result' if passed else 'failure'
    if not (OUT/(label+'.json')).exists():w.save(OUT,label,{'passed':passed,'execution':execution,'image':image,'proof':proof,'saved_job_unchanged':True})
    print(json.dumps({'passed':passed,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
