"""Preserved one-off schema preflight, rollback rehearsal and exact apply."""
import hashlib,json,sys
from pathlib import Path
import release_report_handover_20261011 as release
import control_upgrade_g04_preflight_20261010 as b
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'.work/platform-upgrade-20261011-stage44'
FILES=('scripts/control_report_handover_migration_20261011.py','scripts/migrate_report_handover_20261011.py','migrations/postgres/062_report_design_updates.sql')
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
def main(action,phase):
    if action not in ('start','read') or phase not in ('preflight','rehearse','apply'):raise ValueError('Invalid operation')
    release.unchanged();out=STAGE/('schema-'+phase+'-v1')
    qa=protected(ROOT/'.work/platform-upgrade-20261011-stage42/handover-qa-v1/result.json')
    if not qa['passed'] or qa['proof']['schema_migration_sha256']!=hashes()[FILES[2]]:raise ValueError('Qualified exact migration required')
    job=b.request('GET',b.PATH+b.API)
    if action=='start':
        if out.exists():raise ValueError('Reconcile existing attempt; never redispatch')
        expected=None
        if phase!='preflight':
            preflight=protected(STAGE/'schema-preflight-v1/result.json')
            if not preflight['passed']:raise ValueError('Passing preflight required')
            expected=preflight['proof']['before']
        if phase=='apply':
            rehearsal=protected(STAGE/'schema-rehearse-v1/result.json')
            if not rehearsal['passed'] or not rehearsal['proof']['rollback_verified'] or protected(STAGE/'schema-rehearse-v1/intent.json')['sources']!=hashes():raise ValueError('Exact rollback rehearsal required')
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        code='PHASE='+repr(phase)+'\nEXPECTED='+repr(expected)+'\nMIGRATION_SQL='+repr((ROOT/FILES[2]).read_text())+'\n'+(ROOT/FILES[1]).read_text()
        template=b.corrected_template(job,code);c=template['containers'][0];c['image']=release.BASE
        c['env']=[e for e in c.get('env',[]) if e['name']=='DATABASE_ADMIN_URL']
        if len(c['env'])!=1 or not c['env'][0].get('secretRef') or c['env'][0].get('value'):raise ValueError('Existing admin secret reference only')
        save(out,'intent',{'phase':phase,'sources':hashes(),'template':template,'qa_receipt':qa['protected_receipt'],
            'scope':'Exact additive migration 062 and its ledger row only. No report, approval, broker or portfolio data changes. Three new workflow tables remain dormant.',
            'recovery':'Rollback uncommitted DDL on any error. If website rollout fails after schema commit, retain backward-compatible additive schema and restore prior website image; do not drop audit/history-bearing objects.'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(out,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=protected(out/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Frozen source changed')
    name=protected(out/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env',[]),wanted['env']);status=run['properties']['status']
    if status not in ('Succeeded','Failed'):print(status);return
    prefix='HANDOVER_MIGRATION_FINAL ' if status=='Succeeded' else 'HANDOVER_MIGRATION_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting exact proof; do not redispatch');return
    proof=json.loads(rows[0]['Result']);passed=status=='Succeeded' and proof.get('passed') is True
    if passed and (proof['phase']!=phase or proof['migration_sha256']!=hashes()[FILES[2]] or proof['report_data_rows_changed']!=0 or proof['approval_changes']!=0):raise ValueError('Wrong scope')
    label='result' if passed else 'failure'
    if not (out/(label+'.json')).exists():save(out,label,{'passed':passed,'execution':name,'proof':proof})
    print(json.dumps({'passed':passed,'phase':phase,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
