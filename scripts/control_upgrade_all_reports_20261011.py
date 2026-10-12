"""Run all-five installed reporting acceptance in isolated QA, never production."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_worker_20261010 as b
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
ROOT=b.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage32/all-reports-v1'

def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'probe_upgrade_all_reports_20261011.py','probe_upgrade_g04_worker_20261010.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json')
    snapshots=protected(ROOT/'.work/platform-upgrade-20261011-stage31/fresh-snapshots-v1/result.json')
    job=b.p.request('GET',b.PATH+b.p.API)
    if b.p.bindings([job])[b.JOB]!=baseline['bindings'][b.JOB] or not snapshots['passed']:raise ValueError('Saved worker or snapshot evidence differs')
    if stage=='start':
        if OUT.exists():raise ValueError('Reconcile prior trial; never rerun blindly')
        b.p.runtime.safe_window();b.p.runtime.idle(b.JOB)
        if b.p.bindings(b.p.current_resources())!=baseline['bindings']:raise ValueError('Live workload drift')
        code='import types,sys\nq=types.ModuleType("qa_boundary");sys.modules[q.__name__]=q\nexec('+repr((ROOT/'scripts/probe_upgrade_g04_worker_20261010.py').read_text(encoding='utf-8'))+',q.__dict__)\n'
        code+='EXPECTED_WORKER_SHA='+repr(hashlib.sha256(b.WORKER.read_text(encoding='utf-8').encode()).hexdigest())+'\n'
        expected={r['subject']:{'snapshot_id':r['snapshot_id'],'sha256':r['sha256']} for r in snapshots['proof']['subjects']}
        code+='EXPECTED_SNAPSHOTS='+repr(expected)+'\n'+(ROOT/'scripts/probe_upgrade_all_reports_20261011.py').read_text(encoding='utf-8')
        template=b.p.corrected_template(job,code)
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.p.bindings([job]),'scope':'Generate ten private QA reports and five unchanged replays using installed worker and fresh QA snapshots. Preserve prior QA blobs. No production connection, source writes, report publication or approval changes.',
            'snapshot_evidence':snapshots['protected_receipt'],'full_upgrade_complete':False})
        ack=b.p.request('POST',b.PATH+'/start'+b.p.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.p.request('GET',b.PATH+'/executions/'+name+b.p.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution drift')
    exact_environment(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_ALL_REPORTS_FINAL ' if state=='Succeeded' else 'UPGRADE_ALL_REPORTS_ERROR '
    rows=b.p.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting proof ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state=='Failed':
        if not (OUT/'failure.json').exists():save(OUT,'failure',{'execution':name,'proof':proof,'reconcile_before_retry':True})
        print(json.dumps({'failed':True,'proof':proof}));return
    if proof.get('passed') is not True or proof.get('fresh_artifacts')!=10 or proof.get('idempotent_replays')!=5 or proof.get('database')!='upgrade_g04_fresh_20261011' or proof.get('production_connection_used') is not False or proof.get('canonical_report_writes')!=0 or not proof.get('snapshots_and_approvals_preserved'):raise ValueError('Incomplete isolation or runtime acceptance')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof,'saved_job_unchanged':True,'full_upgrade_complete':False})
    print(json.dumps({'passed':True,'fresh_artifacts':10,'idempotent_replays':5,'visual_acceptance':'UNVERIFIED','numerical_acceptance':'UNVERIFIED'}))

if __name__=='__main__':main(*sys.argv[1:])
