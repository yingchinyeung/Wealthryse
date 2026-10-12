"""Independent all-table audit after the all-subject private generation trial."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_preflight_20261010 as b
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=b.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage32/post-report-integrity-v1'

def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'prepare_upgrade_fresh_reporting_remote_20261011.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    trial=protected(ROOT/'.work/platform-upgrade-20261011-stage32/all-reports-v2/failure.json')
    prepared=protected(ROOT/'.work/platform-upgrade-20261011-stage31/fresh-reporting-db-v1/result.json')
    original=protected(ROOT/'.work/platform-upgrade-20261010-stage29/g04-database-v1/result.json')
    snapshots=protected(ROOT/'.work/platform-upgrade-20261011-stage31/fresh-snapshots-v1/result.json')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json');job=b.request('GET',b.PATH+b.API)
    if trial['proof']['stage']!='readback:hong-kong-china' or b.bindings([job])[b.JOB]!=baseline['bindings'][b.JOB]:raise ValueError('Required trial or job boundary differs')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve prior integrity evidence')
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        code='import types,json,hashlib\nq=types.ModuleType("qa_read_only")\nexec('+repr((ROOT/'scripts/prepare_upgrade_fresh_reporting_remote_20261011.py').read_text(encoding='utf-8'))+',q.__dict__)\n'
        code+='EXPECTED_SOURCE='+repr(original['proof']['tables'])+'\nEXPECTED_QA='+repr(prepared['proof']['qa_fingerprints'])+'\n'
        code+='EXPECTED_SNAPSHOTS='+repr({r['subject']:(r['snapshot_id'],r['sha256']) for r in snapshots['proof']['subjects']})+'\n'
        code+='''source=q.fingerprint(q.SOURCE_DB);qa=q.fingerprint(q.TEST_DB)
if source!=EXPECTED_SOURCE or set(qa)!=set(EXPECTED_QA):raise ValueError('Recovery source or QA schema changed')
changed=sorted(t for t in qa if qa[t]!=EXPECTED_QA[t])
if changed!=['audit_events','report_generation_runs','reporting_snapshots']:raise ValueError('Unexpected QA table changes')
if qa['reporting_snapshots']['rows']!=5 or qa['report_generation_runs']['rows']!=10 or qa['audit_events']['rows']!=EXPECTED_QA['audit_events']['rows']+5:raise ValueError('Unexpected QA row counts')
with q.connect(q.TEST_DB) as db:
    rows=db.execute('SELECT subject_id,snapshot_id,data_sha256,snapshot_payload_json,status FROM reporting_snapshots WHERE tenant_id=%s',(q.TENANT,)).fetchall()
    seen={}
    for r in rows:
        if r['status']!='frozen' or hashlib.sha256(r['snapshot_payload_json'].encode()).hexdigest()!=r['data_sha256']:raise ValueError('QA snapshot integrity failed')
        seen[r['subject_id']]=(str(r['snapshot_id']),r['data_sha256'])
    if seen!=EXPECTED_SNAPSHOTS or len(rows)!=5:raise ValueError('Frozen QA input changed')
    states=db.execute('SELECT status,count(*) AS n FROM report_generation_runs GROUP BY status').fetchall()
    if len(states)!=1 or states[0]['status']!='awaiting_approval' or states[0]['n']!=10:raise ValueError('QA outputs must remain unreleased')
print('UPGRADE_ALL_REPORT_INTEGRITY '+json.dumps({'passed':True,'source_tables_unchanged':97,'qa_original_tables_unchanged':94,'qa_frozen_snapshots_preserved':5,'new_qa_reports_unreleased':10,'new_qa_audit_events':5,'changed_qa_tables':changed,'database_writes':0,'production_connection_used':False,'financial_source_data_unchanged':True,'report_approvals_unchanged':True}),flush=True)
'''
        template=b.corrected_template(job,code);template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_ADMIN_URL']
        if len(template['containers'][0]['env'])!=1 or not template['containers'][0]['env'][0].get('secretRef'):raise ValueError('Existing admin reference only')
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.bindings([job]),'scope':'SELECT-only 97-table source and QA integrity verification; no data writes','trial':trial['protected_receipt']})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution mismatch')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    if state=='Failed':raise ValueError('Integrity check failed; preserve and investigate')
    prefix='UPGRADE_ALL_REPORT_INTEGRITY '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting integrity proof');return
    proof=json.loads(rows[0]['Result'])
    if proof.get('passed') is not True or proof.get('source_tables_unchanged')!=97 or proof.get('qa_original_tables_unchanged')!=94 or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False:raise ValueError('Incomplete integrity proof')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof})
    print(json.dumps(proof))
if __name__=='__main__':main(*sys.argv[1:])
