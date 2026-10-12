"""Independently fingerprint original recovery and new QA after snapshot creation."""
import hashlib,json,sys
import control_upgrade_g04_preflight_20261010 as b
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=b.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage31/fresh-snapshot-integrity-v1'
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in ('control_upgrade_fresh_snapshot_integrity_20261011.py','prepare_upgrade_fresh_reporting_remote_20261011.py')}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    trial=protected(ROOT/'.work/platform-upgrade-20261011-stage31/fresh-snapshots-v1/result.json')
    prepared=protected(ROOT/'.work/platform-upgrade-20261011-stage31/fresh-reporting-db-v1/result.json')
    original=protected(ROOT/'.work/platform-upgrade-20261010-stage29/g04-database-v1/result.json')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json')
    job=b.request('GET',b.PATH+b.API)
    if not trial['passed'] or not prepared['passed'] or b.bindings([job])[b.JOB]!=baseline['bindings'][b.JOB]:raise ValueError('Passed trial and unchanged worker required')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve previous integrity trial')
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        code='import types,json\nm=types.ModuleType("qa_read_only")\nexec('+repr((ROOT/'scripts/prepare_upgrade_fresh_reporting_remote_20261011.py').read_text(encoding='utf-8'))+',m.__dict__)\n'
        code+='EXPECTED_SOURCE='+repr(original['proof']['tables'])+'\nEXPECTED_QA='+repr(prepared['proof']['qa_fingerprints'])+'\n'
        code+='''source=m.fingerprint(m.SOURCE_DB)
qa=m.fingerprint(m.TEST_DB)
if source!=EXPECTED_SOURCE or set(qa)!=set(EXPECTED_QA):raise ValueError('Recovery source or QA schema changed')
changed=sorted(t for t in qa if qa[t]!=EXPECTED_QA[t])
if changed!=['reporting_snapshots'] or qa['reporting_snapshots']['rows']!=5:raise ValueError('Unexpected QA changes')
def snapshots(database):
    with m.connect(database) as db:
        rows=db.execute("SELECT subject_id,snapshot_payload_json FROM reporting_snapshots WHERE tenant_id=%s AND period_end='2026-09-30' AND status='frozen'",(m.TENANT,)).fetchall()
        return {r['subject_id']:json.loads(r['snapshot_payload_json']) for r in rows}
def changes(a,b,path=''):
    if type(a)!=type(b):return [path]
    if isinstance(a,dict):
        result=[]
        for key in sorted(set(a)|set(b)):
            location=path+'.'+key if path else key
            result += [location] if key not in a or key not in b else changes(a[key],b[key],location)
        return result
    if isinstance(a,list):
        if len(a)!=len(b):return [path+'.length']
        result=[]
        for i,(x,y) in enumerate(zip(a,b)):result+=changes(x,y,path+'['+str(i)+']')
        return result
    return [] if a==b else [path]
old=snapshots(m.SOURCE_DB);new=snapshots(m.TEST_DB)
if set(old)!=set(new) or len(new)!=5:raise ValueError('Five comparable monthly inputs required')
comparison={s:changes(old[s],new[s]) for s in sorted(new)}
summary={s:{'changed_leaf_count':len(paths),'changed_paths':paths[:100],'paths_truncated':len(paths)>100} for s,paths in comparison.items()}
print('UPGRADE_FRESH_SNAPSHOT_INTEGRITY '+json.dumps({'passed':True,'source_unchanged':True,'tables_checked':97,'qa_tables_unchanged':96,'changed_qa_tables':changed,'new_snapshots':5,'database_writes':0,'production_connection_used':False,'reports_and_approvals_unchanged':True,'comparison_to_preexisting_frozen_inputs':summary,'financial_values_retained':False}),flush=True)
'''
        template=b.corrected_template(job,code);template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_ADMIN_URL']
        if len(template['containers'][0]['env'])!=1 or not template['containers'][0]['env'][0].get('secretRef'):raise ValueError('Existing admin reference only')
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.bindings([job]),'scope':'SELECT-only 97-table integrity check; no production connection or writes'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    if state=='Failed':raise ValueError('Integrity probe failed; inspect before any write')
    prefix='UPGRADE_FRESH_SNAPSHOT_INTEGRITY '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting integrity proof');return
    proof=json.loads(rows[0]['Result'])
    if proof.get('passed') is not True or proof.get('source_unchanged') is not True or proof.get('qa_tables_unchanged')!=96 or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False:raise ValueError('Integrity proof incomplete')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof,'saved_job_unchanged':True})
    print(json.dumps(proof))

if __name__=='__main__':main(*sys.argv[1:])
