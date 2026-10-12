"""Post-trial full-table integrity verification, SELECT only on recovery and QA."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_preflight_20261010 as p
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=p.ROOT;OUT=ROOT/'.work/platform-upgrade-20261010-stage29/post-trial-integrity-v1'


def hashes():
    return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in
        (Path(__file__).name,'create_upgrade_g04_database_remote_20261010.py')}


def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    baseline=protected(ROOT/'.work/platform-upgrade-20261010-stage29/g04-database-v1/result.json')
    trial=protected(ROOT/'.work/platform-upgrade-20261010-stage29/fresh-report-v1/result.json')
    if baseline.get('passed') is not True or trial.get('passed') is not True:raise ValueError('Passed trial required')
    original=protected(p.OUT/'intent.json');job=p.request('GET',p.PATH+p.API)
    if p.bindings([job])!=original['binding']:raise ValueError('Bootstrap drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve prior trial')
        p.runtime.safe_window();p.runtime.idle(p.JOB)
        module=(ROOT/'scripts/create_upgrade_g04_database_remote_20261010.py').read_text(encoding='utf-8')
        code='import types,json\nm=types.ModuleType("readonly_fingerprints")\nexec('+repr(module)+',m.__dict__)\nEXPECTED='+repr(baseline['proof']['tables'])+'\n'
        code+='''source=m.fingerprint(m.SOURCE_DB)
qa=m.fingerprint(m.TEST_DB)
if source!=EXPECTED:raise ValueError('Recovery source changed')
if set(qa)!=set(EXPECTED):raise ValueError('QA schema changed')
changed=sorted(name for name in qa if qa[name]!=EXPECTED[name])
if changed!=['audit_events','report_generation_runs']:raise ValueError('Unexpected QA table changes')
if qa['report_generation_runs']['rows']!=EXPECTED['report_generation_runs']['rows']+2 or qa['audit_events']['rows']!=EXPECTED['audit_events']['rows']+1:raise ValueError('Unexpected QA generation record count')
print('UPGRADE_G04_INTEGRITY '+json.dumps({'passed':True,'source_unchanged':True,'tables_checked':len(qa),'changed_qa_tables':changed,'qa_financial_data_unchanged':True,'qa_snapshots_and_approvals_unchanged':True,'database_writes':0,'production_connection_used':False}),flush=True)
'''
        template=p.corrected_template(job,code);template['containers'][0]['env']=original['template']['containers'][0]['env']
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':p.bindings([job]),'scope':'SELECT-only all-table fingerprints; no production connection',
            'baseline':baseline['protected_receipt'],'trial':trial['protected_receipt']})
        ack=p.request('POST',p.PATH+'/start'+p.API,data=template)
        save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes() or intent['binding']!=p.bindings([job]):raise ValueError('Source or job drift')
    name=protected(OUT/'dispatch.json')['execution'];run=p.request('GET',p.PATH+'/executions/'+name+p.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(p.JOB,actual.get('env') or [],wanted['env'])
    state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    if state=='Failed':raise ValueError('Integrity verification failed; investigate without mutation')
    prefix='UPGRADE_G04_INTEGRITY '
    rows=p.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting proof ingestion');return
    result=json.loads(rows[0]['Result'])
    if result.get('passed') is not True or result.get('source_unchanged') is not True or result.get('tables_checked')!=97 or result.get('database_writes')!=0 or result.get('production_connection_used') is not False:raise ValueError('Integrity proof incomplete')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':result,'saved_job_unchanged':True})
    print(json.dumps(result))


if __name__=='__main__':main(*sys.argv[1:])
