"""SELECT-only postflight using the installed assurance image and its normal DB reference."""
import hashlib,json,sys
from pathlib import Path
import deploy_reporting_sector_assurance_20261011 as a
from read_reporting_web_installed_receipt_20261011 import validate_env
b=a.b;OUT=a.w.STAGE/'assurance-installed-role-v1'

def sources():return {n:hashlib.sha256((a.w.ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'probe_reporting_assurance_installed_20261011.py','read_reporting_web_installed_receipt_20261011.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Exact postflight only')
    verified=a.protected(a.OUT/'verified.json');plan=a.protected(a.OUT/'plan.json')
    image,source,_=a.w.candidate('assurance')
    job=b.request('GET',a.PATH+b.API)
    if not verified['passed'] or verified['image']!=image or job['properties']['template']['containers'][0]['image']!=image or a.settings_invariant(job)!=plan['invariant']:raise ValueError('Installed worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve prior execution')
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        a.only_assurance(b.bindings(b.current_resources()),plan['bindings'])
        code='import sys\nsys.path[:0]=["/app/src","/app/scripts"]\nSOURCE_HASHES='+repr(source['candidate_hashes'])+'\nIMAGE='+repr(image)+'\n'+(a.w.ROOT/'scripts/probe_reporting_assurance_installed_20261011.py').read_text()
        template=b.corrected_template(job,code);container=template['containers'][0]
        container['env']=[e for e in container.get('env',[]) if e['name'] in ('DATABASE_URL','PORTFOLIO_TENANT_ID')]
        validate_env(a.JOB,container['env'],container['env'])
        a.save(OUT,'intent',{'template':template,'sources':sources(),'image':image,'scope':'Five read-only sector requests under the configured non-privileged worker DB role; no model calls, writes, saved-job or approval changes.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);a.save(OUT,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=a.protected(OUT/'intent.json')
    if intent['sources']!=sources():raise ValueError('Postflight source drift')
    name=a.protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Executed template differs')
    validate_env(a.JOB,actual.get('env',[]),wanted['env']);status=run['properties']['status']
    if status not in ('Succeeded','Failed'):print(status);return
    prefix='REPORTING_ASSURANCE_INSTALLED_FINAL ' if status=='Succeeded' else 'REPORTING_ASSURANCE_INSTALLED_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting proof');return
    proof=json.loads(rows[0]['Result']);passed=status=='Succeeded'
    if passed and (not proof['passed'] or proof['image']!=image or proof['source_hashes']!=source['candidate_hashes'] or len(proof['subjects'])!=5 or not all(r['passed'] for r in proof['subjects']) or proof['database_writes']!=0 or proof['privileged_role'] is not False or not proof['database_readonly'] or not proof['verified_tls']):raise ValueError('Incomplete role postflight')
    label='result' if passed else 'failure'
    if not (OUT/(label+'.json')).exists():a.save(OUT,label,{'passed':passed,'execution':name,'image':image,'proof':proof,'saved_job_unchanged':True,'full_scheduled_cycle':'UNVERIFIED'})
    print(json.dumps({'passed':passed,'proof':proof}))

if __name__=='__main__':main(*sys.argv[1:])
