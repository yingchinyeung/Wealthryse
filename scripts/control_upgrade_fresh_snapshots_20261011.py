"""Run installed snapshot routines in a new isolated QA database only."""
import hashlib,json,sys
import control_upgrade_g04_worker_20261010 as b
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=b.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage31/fresh-snapshots-v1'
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in ('control_upgrade_fresh_snapshots_20261011.py','probe_upgrade_fresh_snapshots_20261011.py')}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    prepared=protected(ROOT/'.work/platform-upgrade-20261011-stage31/fresh-reporting-db-v1/result.json')
    if not prepared['passed'] or prepared['proof']['database']!='upgrade_g04_fresh_20261011':raise ValueError('Prepared isolated copy required')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json');job=b.p.request('GET',b.PATH+b.p.API)
    if b.p.bindings([job])[b.JOB]!=baseline['bindings'][b.JOB]:raise ValueError('Worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Reconcile previous run; no blind retry')
        b.p.runtime.safe_window();b.p.runtime.idle(b.JOB)
        code='EXPECTED_WORKER_SHA='+repr(hashlib.sha256(b.WORKER.read_text(encoding='utf-8').encode()).hexdigest())+'\n'+(ROOT/'scripts/probe_upgrade_fresh_snapshots_20261011.py').read_text(encoding='utf-8')
        template=b.p.corrected_template(job,code);template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_URL']
        if len(template['containers'][0]['env'])!=1 or not template['containers'][0]['env'][0].get('secretRef'):raise ValueError('Existing DB reference required')
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.p.bindings([job]),'prepared':prepared['protected_receipt'],
            'scope':'Five new September snapshots and unchanged-input replay in isolated fresh QA database; no production, report generation or approval changes'})
        ack=b.p.request('POST',b.PATH+'/start'+b.p.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.p.request('GET',b.PATH+'/executions/'+name+b.p.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_FRESH_SNAPSHOTS ' if state=='Succeeded' else 'UPGRADE_FRESH_SNAPSHOTS_ERROR '
    rows=b.p.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting snapshot proof');return
    proof=json.loads(rows[0]['Result'])
    if state=='Failed':
        if not (OUT/'failure.json').exists():save(OUT,'failure',{'execution':name,'proof':proof})
        print(json.dumps({'failed':True,'proof':proof}));return
    if proof.get('passed') is not True or proof.get('database')!='upgrade_g04_fresh_20261011' or len(proof.get('subjects',[]))!=5 or not proof.get('unchanged_replay_passed') or proof.get('production_connection_used') is not False:raise ValueError('Snapshot acceptance incomplete')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof,'saved_job_unchanged':True,'g04_complete':False})
    print(json.dumps(proof))

if __name__=='__main__':main(*sys.argv[1:])
