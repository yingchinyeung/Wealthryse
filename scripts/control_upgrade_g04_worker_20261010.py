"""Run a pinned installed-worker trial without changing the saved job."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_preflight_20261010 as p
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=p.ROOT;OUT=ROOT/'.work/platform-upgrade-20261010-stage29/worker-preflight-v1'
JOB='ibkrv2stg-month-end-factsheet';PATH=p.rid('Microsoft.App/jobs',JOB)
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:0269974da175ac319f552ab9b403e309c1d116ed8554a8ceb7fce4e0bcf7b494'
WORKER=ROOT/'.work/platform-upgrade-20261009-stage3/worker-release-v3/context/worker/generate_month_end_package.py'


def sources():
    return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in
        (Path(__file__).name,'probe_upgrade_g04_worker_20261010.py')}


def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    clone=protected(ROOT/'.work/platform-upgrade-20261010-stage29/g04-database-v1/result.json')
    if clone.get('passed') is not True or not clone['proof']['copy_identical']:raise ValueError('Qualified QA database required')
    job=p.request('GET',PATH+p.API)
    if job['properties']['template']['containers'][0]['image']!=IMAGE:raise ValueError('Installed image drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Reconcile previous execution')
        p.runtime.safe_window();p.runtime.idle(JOB)
        code='MODE="preflight"\nEXPECTED_WORKER_SHA='+repr(hashlib.sha256(WORKER.read_text(encoding='utf-8').encode()).hexdigest())+'\n'+(ROOT/'scripts/probe_upgrade_g04_worker_20261010.py').read_text(encoding='utf-8')
        template=p.corrected_template(job,code)
        # Secrets stay existing job references; never copy resolved values.
        if any(e.get('value') for e in template['containers'][0]['env'] if e.get('secretRef')):raise ValueError('Secret material not allowed')
        save(OUT,'intent',{'sources':sources(),'binding':p.bindings([job]),'template':template,
            'scope':'QA-only app-role metadata, session lock contention/recovery and negative governed request; no report creation or publication'})
        ack=p.request('POST',PATH+'/start'+p.API,data=template)
        save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=sources() or intent['binding']!=p.bindings([job]):raise ValueError('Source or saved job drift')
    name=protected(OUT/'dispatch.json')['execution'];run=p.request('GET',PATH+'/executions/'+name+p.API)
    wanted=intent['template']['containers'][0];actual=run['properties']['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(JOB,actual.get('env') or [],wanted['env'])
    state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_G04_WORKER ' if state=='Succeeded' else 'UPGRADE_G04_WORKER_ERROR '
    rows=p.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting bounded result ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state=='Failed':print(json.dumps({'failed':True,'proof':proof}));return
    for key in ('passed','concurrency_rejected_across_commit','lock_recovered_on_close','governed_request_rejected_by_normal_worker','qa_report_state_unchanged'):
        if proof.get(key) is not True:raise ValueError('Acceptance failed: '+key)
    if proof.get('production_connection_used') is not False or proof.get('database')!='upgrade_g04_20261010':raise ValueError('Isolation failed')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof,'saved_job_unchanged':True,'g04_complete':False})
    print(json.dumps({'passed':True,'concurrency_and_lock_recovery':True,'fresh_generation_tested':False}))


if __name__=='__main__':main(*sys.argv[1:])
