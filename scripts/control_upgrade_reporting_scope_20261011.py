"""All-subject read-only QA preflight; reuse worker image but never normal workflow."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_worker_20261010 as b
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=b.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage31/reporting-scope-v1'

def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'probe_upgrade_reporting_scope_20261011.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json')
    job=b.p.request('GET',b.PATH+b.p.API)
    if b.p.bindings([job])[b.JOB]!=baseline['bindings'][b.JOB]:raise ValueError('Reporting worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Reconcile previous trial')
        b.p.runtime.safe_window();b.p.runtime.idle(b.JOB)
        template=b.p.corrected_template(job,(ROOT/'scripts/probe_upgrade_reporting_scope_20261011.py').read_text(encoding='utf-8'))
        template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_URL']
        if len(template['containers'][0]['env'])!=1 or not template['containers'][0]['env'][0].get('secretRef'):raise ValueError('Existing DB reference only')
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.p.bindings([job]),'scope':'SELECT-only five-subject QA frozen-input integrity and report-metadata dependency graph; no publication or financial writes'})
        ack=b.p.request('POST',b.PATH+'/start'+b.p.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.p.request('GET',b.PATH+'/executions/'+name+b.p.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env'])
    status=run['properties']['status']
    if status not in ('Succeeded','Failed'):print(status);return
    prefix='UPGRADE_REPORTING_SCOPE ' if status=='Succeeded' else 'UPGRADE_REPORTING_SCOPE_ERROR '
    rows=b.p.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting report-scope proof');return
    proof=json.loads(rows[0]['Result'])
    if status=='Failed':
        if not (OUT/'failure.json').exists():save(OUT,'failure',{'execution':name,'proof':proof})
        print(json.dumps({'failed':True,'proof':proof}));return
    if proof.get('passed') is not True or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False or len(proof.get('subjects',[]))!=5:raise ValueError('Incomplete scope proof')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof,'saved_job_unchanged':True,'g04_complete':False})
    print(json.dumps(proof))

if __name__=='__main__':main(*sys.argv[1:])
