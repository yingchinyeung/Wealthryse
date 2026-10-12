"""Predeployment full collector trial using only the job's existing restricted role."""
import hashlib,json,sys
from pathlib import Path
import control_snapshot_correction_20261011 as q
from read_reporting_web_installed_receipt_20261011 import validate_env

b=q.b;JOB=q.release.JOB;PATH=b.rid('Microsoft.App/jobs',JOB);OUT=q.STAGE/'snapshot-normal-role-v1'

def sources():return {n:hashlib.sha256((q.ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'probe_snapshot_role_20261011.py','read_reporting_web_installed_receipt_20261011.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Exact role trial required')
    image,source=q.candidate('candidate');job=b.request('GET',PATH+b.API)
    baseline=q.protected(q.STAGE/'resume-v1/result.json')['boundary']['workloads']
    if b.bindings([job])[JOB]!=baseline[JOB]:raise ValueError('Original snapshot job changed')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve existing trial')
        b.runtime.safe_window();b.runtime.idle(JOB);q.release.unchanged()
        code='import sys\nsys.path[:0]=["/app/src","/app/scripts"]\nSOURCE_HASHES='+repr(source)+'\nIMAGE='+repr(image)+'\n'+(q.ROOT/'scripts/probe_snapshot_role_20261011.py').read_text()
        template=b.corrected_template(job,code);container=template['containers'][0];container['image']=image
        container['env']=[e for e in container.get('env',[]) if e['name'] in ('DATABASE_URL','PORTFOLIO_TENANT_ID')]
        validate_env(JOB,container['env'],container['env'])
        q.save(OUT,'intent',{'template':template,'sources':sources(),'source_hashes':source,'image':image,'scope':'Full five-subject collector under existing normal snapshot role. SELECT-only verified TLS. No persistent image update, snapshot freezing, report generation or approval mutation.'})
        ack=b.request('POST',PATH+'/start'+b.API,data=template);q.save(OUT,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=q.protected(OUT/'intent.json')
    if intent['sources']!=sources() or intent['source_hashes']!=source:raise ValueError('Trial source drift')
    name=q.protected(OUT/'dispatch.json')['execution'];run=b.request('GET',PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution changed')
    validate_env(JOB,actual.get('env',[]),wanted['env']);status=run['properties']['status']
    if status not in ('Succeeded','Failed'):print(status);return
    prefix='SNAPSHOT_ROLE_FINAL ' if status=='Succeeded' else 'SNAPSHOT_ROLE_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting proof');return
    proof=json.loads(rows[0]['Result']);passed=status=='Succeeded'
    if passed and (proof.get('passed') is not True or proof['image']!=image or proof['source_hashes']!=source or len(proof['subjects'])!=5 or {r['subject'] for r in proof['subjects']}!=q.SUBJECTS or not all(r['passed'] for r in proof['subjects']) or proof['database_writes']!=0 or proof['privileged_role'] is not False or not proof['verified_tls'] or not proof['database_readonly']):raise ValueError('Incomplete normal-role proof')
    label='result' if passed else 'failure'
    if not (OUT/(label+'.json')).exists():q.save(OUT,label,{'passed':passed,'image':image,'proof':proof,'execution':name,'saved_job_unchanged':True})
    print(json.dumps({'passed':passed,'subjects':len(proof.get('subjects',[])),'error':proof.get('message')}))

if __name__=='__main__':main(sys.argv[1])
