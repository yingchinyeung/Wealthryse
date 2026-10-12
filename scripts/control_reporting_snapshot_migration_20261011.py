"""Qualify migration only on the separately created private QA clone."""
import hashlib,json,sys
from pathlib import Path
import control_reporting_migration_clone_20261011 as base
import release_reporting_corrections_20261011 as release
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=base.ROOT;b=base.b;OUT=base.OUT.parent/'snapshot-migration-v1'
MODULES=['prepare_reporting_migration_clone_20261011','legacy_reporting_reconciliation_20261011','prepare_reporting_snapshot_correction_20261011',
         'test_reporting_migration_clone_20261011','test_legacy_reporting_reconciliation_20261011','test_reporting_snapshot_correction_20261011']
FILES=[Path(__file__).name,'rehearse_reporting_snapshot_migration_20261011.py']+[n+'.py' for n in MODULES]
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    baseline=protected(OUT.parent/'resume-v1/result.json');job=b.request('GET',b.PATH+b.API)
    if b.bindings([job])[b.JOB]!=baseline['boundary']['workloads'][b.JOB]:raise ValueError('Saved job drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve and reconcile previous migration')
        clone=protected(base.OUT/'result.json');review=protected(OUT.parent/'image-review-v1/result.json')
        prior=protected(OUT.parent/'historical-reconciled-read-v1/result.json');raw=(OUT.parent/'historical-reconciled-read-v1/inventory.json').read_bytes()
        if not clone['passed'] or not review['passed'] or prior['blockers'] or hashlib.sha256(raw).hexdigest()!=prior['inventory_sha256']:raise ValueError('Passing clone, candidate and historical plan required')
        source=release.r.read(release.OUT/'source-intent.json')['candidate_hashes']
        code='import types,sys\nsys.path[:0]=["/app/src","/app/scripts"]\n'
        for name in MODULES:
            code+='m=types.ModuleType('+repr(name)+');sys.modules[m.__name__]=m\nexec('+repr((ROOT/'scripts'/(name+'.py')).read_text(encoding='utf-8'))+',m.__dict__)\n'
        code+='SOURCE_HASHES='+repr(source)+'\nBASELINE='+repr(clone['proof']['all_tables'])+'\nINVENTORY='+repr(json.loads(raw))+'\nTEST_MODULES='+repr(MODULES[3:])+'\n'
        code+=(ROOT/'scripts'/FILES[1]).read_text(encoding='utf-8')
        template=b.corrected_template(job,code);template['containers'][0]['image']=review['image']
        env=template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_ADMIN_URL']
        if len(env)!=1 or not env[0].get('secretRef') or env[0].get('value'):raise ValueError('Existing admin reference only')
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        if b.bindings(b.current_resources())!=baseline['boundary']['workloads']:raise ValueError('Workload drift')
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.bindings([job]),'image':review['image'],'inventory_sha256':prior['inventory_sha256'],
            'scope':'Only upgrade_g04_migration_20261011: insert 63 versioned snapshots, test transaction rollback and committed status rollback, leave corrected versions active for private report QA. Preserve all original content, report approvals and other 96 tables. No production connection, saved job update, broker mutation or publication.'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes() or intent['binding']!=b.bindings([job]):raise ValueError('Migration source/job drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='REPORTING_MIGRATION_FINAL ' if state=='Succeeded' else 'REPORTING_MIGRATION_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting migration receipt');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (not proof.get('passed') or proof.get('database')!='upgrade_g04_migration_20261011' or proof.get('corrected_snapshots')!=63 or proof.get('active_snapshots')!=105 or not proof.get('transactional_rollback_passed') or not proof.get('committed_rollback_passed') or proof.get('non_snapshot_tables_unchanged')!=96 or proof.get('production_connection_used') is not False):raise ValueError('Incomplete migration proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'deployed':False,'saved_job_unchanged':True})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
