"""Scanned native candidate, rollback-only migration trial on private QA."""
import hashlib,json,sys
from pathlib import Path
import release_report_handover_20261011 as release
import control_upgrade_g04_preflight_20261010 as b
import scan_ms_presentation_worker_candidate as scan
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage42/handover-qa-v1'
FILES=('scripts/control_report_handover_qa_20261011.py','scripts/rehearse_report_handover_schema_20261011.py',
       'scripts/prepare_reporting_migration_clone_20261011.py','migrations/postgres/062_report_design_updates.sql')

def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    release.unchanged();source,_,output=scan.verify(release.OUT)
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    record=json.loads((release.OUT/'scan-completed.json').read_bytes())
    remote,_=restore_protected(json.loads((release.OUT/'scan-protected-receipt.json').read_bytes()))
    if record!=remote or record['image']!=image or record['assessment']['status']!='pass' or record['assessment']['blocking_findings']!=0:raise ValueError('Exact protected passing scan required')
    job=b.request('GET',b.PATH+b.API)
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve trial')
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        code='import types,sys\nsys.path[:0]=["/app/src","/app/scripts"]\nq=types.ModuleType("prepare_reporting_migration_clone_20261011");sys.modules[q.__name__]=q\n'
        code+='exec('+repr((ROOT/FILES[2]).read_text())+',q.__dict__)\n'
        code+='IMAGE='+repr(image)+'\nSOURCE_HASHES='+repr(source['candidate_hashes'])+'\nMIGRATION_SQL='+repr((ROOT/FILES[3]).read_text())+'\n'+(ROOT/FILES[1]).read_text()
        template=b.corrected_template(job,code);container=template['containers'][0];container['image']=image
        container['env']=protected(ROOT/'.work/platform-upgrade-20261011-stage34/august-snapshot-correction-v1/intent.json')['template']['containers'][0]['env']
        if len(container['env'])!=1 or container['env'][0].get('name')!='DATABASE_ADMIN_URL' or not container['env'][0].get('secretRef') or container['env'][0].get('value'):raise ValueError('Existing QA credential reference only')
        save(OUT,'intent',{'template':template,'image':image,'sources':hashes(),'scope':'Private recovery QA database only. Rehearse exact 062 schema within a rolled-back transaction, test candidate helper with synthetic temp tables, verify all 97 original tables unchanged. No production connection, publication or saved-job changes.'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes() or intent['image']!=image:raise ValueError('Frozen runtime drift')
    execution=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+execution+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Wrong execution')
    execution_env(b.JOB,actual.get('env',[]),wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='REPORT_HANDOVER_QA_FINAL ' if state=='Succeeded' else 'REPORT_HANDOVER_QA_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting proof');return
    proof=json.loads(rows[0]['Result']);passed=state=='Succeeded' and proof.get('passed') is True
    if passed:
        if proof['image']!=image or proof['source_hashes']!=source['candidate_hashes'] or proof['schema_migration_sha256']!=hashes()[FILES[3]] or proof['tables_unchanged']!=97 or proof['persistent_database_changes']!=0 or proof['production_connection_used'] is not False:raise ValueError('Incomplete proof')
        if not all(proof[k] is True for k in ('schema_rollback_passed','approval_rollback_passed','cross_scope_isolation_passed','new_tables_force_tenant_rls','integrity_constraints_verified')):raise ValueError('Missing acceptance gate')
    name='result' if passed else 'failure'
    if not (OUT/(name+'.json')).exists():save(OUT,name,{'passed':passed,'image':image,'execution':execution,'proof':proof,'deployed':False})
    print(json.dumps({'passed':passed,'proof':proof}))

if __name__=='__main__':main(sys.argv[1])
