"""Exercise twenty versioned enrichments and rollback in the private QA clone."""
import hashlib,json,sys
from pathlib import Path
import control_reporting_snapshot_migration_20261011 as base
import release_reporting_full_nav_20261011 as release
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected
import scan_ms_presentation_worker_candidate as scan
ROOT=base.ROOT;b=base.b;OUT=base.OUT.parent/'monthly-snapshot-enrichment-v1'
MODULES=['prepare_reporting_migration_clone_20261011','reconcile_legacy_monthly_reporting_20261011','prepare_monthly_snapshot_enrichment_20261011',
         'test_legacy_monthly_reporting_20261011','test_monthly_snapshot_enrichment_20261011','test_reporting_migration_clone_20261011']
FILES=[Path(__file__).name,'rehearse_reporting_snapshot_migration_20261011.py',*[n+'.py' for n in MODULES]]
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}
def runner():
    code=(ROOT/'scripts/rehearse_reporting_snapshot_migration_20261011.py').read_text()
    replacements={
      'from prepare_reporting_snapshot_correction_20261011 import prepare':'''from prepare_monthly_snapshot_enrichment_20261011 import prepare as prepare_enrichment
from ibkr_etl.monthly_reporting_analytics import build_monthly_reporting_analytics
def prepare(row,plan,adapter,normalize,detail,now):
    fresh=build_monthly_reporting_analytics(adapter,subject_id=row['subject_id'],period_end=row['period_end'],reporting_currency=row['reporting_currency'])
    return prepare_enrichment(row,plan,fresh,now)''',
      'tests.testsRun!=46':'tests.testsRun!=41',
      'q.fingerprint(q.SOURCE_DB)!=BASELINE':'q.fingerprint(q.SOURCE_DB)!=SOURCE_BASELINE',
      "expected={r['snapshot_id']:r for r in INVENTORY['rows']}":"expected={r['source_snapshot_id']:r for r in INVENTORY['rows']}",
      'len(expected)!=105':'len(expected)!=20',
      "and r['subject_id'] in {v['subject'] for v in expected.values()}":"and r['subject_id'] in SUBJECTS",
      "if set(active)!=set(expected):raise ValueError('Active QA history differs')":"if len(active)!=105 or not set(expected)<=set(active):raise ValueError('Active QA history differs')",
      'len(pairs)!=63':'len(pairs)!=20',
      'Expected exactly 63 corrected snapshots':'Expected exactly 20 enriched snapshots',
      "'corrected_snapshots':63":"'corrected_snapshots':20",
    }
    counts={'q.fingerprint(q.SOURCE_DB)!=BASELINE':2,"and r['subject_id'] in {v['subject'] for v in expected.values()}":2}
    for old,new in replacements.items():
        if code.count(old)!=counts.get(old,1):raise ValueError('Exact reviewed migration anchor required: '+old)
        code=code.replace(old,new)
    return code
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(OUT.parent/'resume-v1/result.json');job=b.request('GET',b.PATH+b.API)
    if b.bindings([job])[b.JOB]!=baseline['boundary']['workloads'][b.JOB]:raise ValueError('Saved bootstrap drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve earlier rehearsal')
        source,native,image=scan.verify(release.OUT);scanned=release.r.read(release.OUT/'scan-completed.json')
        secured,_=restore_protected(release.r.read(release.OUT/'scan-protected-receipt.json'))
        ref=image['registry']+'/'+image['repository']+'@'+image['digest']
        if secured!=scanned or scanned['image']!=ref or scanned['assessment']['status']!='pass':raise ValueError('Exact passing scan required')
        plan=protected(OUT.parent/'legacy-monthly-plan-v3/result.json');migration=protected(OUT.parent/'snapshot-migration-v2/result.json');clone=protected(OUT.parent/'migration-clone-v1/result.json')
        if not all(p['passed'] for p in (plan,migration,clone)):raise ValueError('Passing private prerequisites required')
        current=dict(clone['proof']['all_tables']);current['reporting_snapshots']=migration['proof']['final_snapshot_fingerprint']
        months=[{**m,'status':'frozen','needs_snapshot_correction':True} for m in plan['proof']['months_detail']]
        if len(months)!=20:raise ValueError('Exact twenty-month plan required')
        code='import types,sys\nsys.path[:0]=["/app/src","/app/scripts"]\n'
        for name in MODULES:code+='m=types.ModuleType('+repr(name)+');sys.modules[m.__name__]=m\nexec('+repr((ROOT/'scripts'/(name+'.py')).read_text())+',m.__dict__)\n'
        code+='SOURCE_HASHES='+repr(source['candidate_hashes'])+'\nSOURCE_BASELINE='+repr(clone['proof']['all_tables'])+'\nBASELINE='+repr(current)+'\nINVENTORY='+repr({'rows':months})+'\nTEST_MODULES='+repr(MODULES[3:])+'\nSUBJECTS='+repr(('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'))+'\n'+runner()
        template=b.corrected_template(job,code);template['containers'][0]['image']=ref
        env=template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_ADMIN_URL']
        if len(env)!=1 or not env[0].get('secretRef') or env[0].get('value'):raise ValueError('Existing secret reference only')
        b.runtime.safe_window();b.runtime.idle(b.JOB);b.runtime.idle('ibkrv2stg-month-end-factsheet')
        if b.bindings(b.current_resources())!=baseline['boundary']['workloads']:raise ValueError('Workload drift')
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.bindings([job]),'image':ref,
            'scope':'Private upgrade_g04_migration_20261011 only. Create twenty versioned monthly-analytics snapshots; transaction and committed rollback, then private reactivation. Preserve original content, approvals, other 96 tables and original QA. No production connection, saved job update or publication.'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes() or intent['binding']!=b.bindings([job]):raise ValueError('Rehearsal source/binding drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='REPORTING_MIGRATION_FINAL ' if state=='Succeeded' else 'REPORTING_MIGRATION_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting receipt');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (not proof['passed'] or proof['native_tests']!=41 or proof['corrected_snapshots']!=20 or proof['active_snapshots']!=105 or not proof['transactional_rollback_passed'] or not proof['committed_rollback_passed'] or proof['non_snapshot_tables_unchanged']!=96 or proof['production_connection_used'] is not False):raise ValueError('Incomplete enrichment proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'image':intent['image'],'deployed':False})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
