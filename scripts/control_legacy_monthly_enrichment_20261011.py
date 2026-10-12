"""Private read-only native qualification of legacy monthly reconstruction."""
import hashlib,json,sys
from pathlib import Path
import control_reporting_migration_clone_20261011 as base
import release_reporting_corrections_20261011 as release
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=base.ROOT;b=base.b;OUT=base.OUT.parent/'legacy-monthly-plan-v1'
MODULES={'prepare_reporting_migration_clone_20261011':'scripts/prepare_reporting_migration_clone_20261011.py',
         'reconcile_legacy_monthly_reporting_20261011':'scripts/reconcile_legacy_monthly_reporting_20261011.py',
         'test_legacy_monthly_reporting_20261011':'scripts/test_legacy_monthly_reporting_20261011.py',
         'test_monthly_reporting_analytics':'tests/test_monthly_reporting_analytics.py'}
FILES=['scripts/'+Path(__file__).name,'scripts/probe_legacy_monthly_enrichment_20261011.py',*MODULES.values()]
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    baseline=protected(OUT.parent/'resume-v1/result.json');job=b.request('GET',b.PATH+b.API)
    if b.bindings([job])[b.JOB]!=baseline['boundary']['workloads'][b.JOB]:raise ValueError('Bootstrap drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve earlier qualification')
        readiness=protected(OUT.parent/'historical-readiness-v2/result.json')['proof'];migration=protected(OUT.parent/'snapshot-migration-v2/result.json');clone=protected(OUT.parent/'migration-clone-v1/result.json')
        review=protected(OUT.parent/'image-review-v1/result.json')
        months=readiness['unready']
        if len(months)!=20 or any(r['subject']!='U16088571' or r['monthly_present'] or not r['rebuilt_available'] or r['rebuilt_quality']!='pass' for r in months):raise ValueError('Exact missing-analytics scope required')
        report_hashes={t:clone['proof']['all_tables'][t]['sha256'] for t in ('onboarding_historical_report_items','report_artifact_releases','report_generation_runs','report_release_decisions','reporting_snapshots')}
        report_hashes['reporting_snapshots']=migration['proof']['final_snapshot_fingerprint']['sha256']
        source=release.r.read(release.OUT/'source-intent.json')['candidate_hashes']
        code='import types,sys\nsys.path[:0]=["/app/src","/app/scripts"]\n'
        for name,path in MODULES.items():code+='m=types.ModuleType('+repr(name)+');sys.modules[m.__name__]=m\nexec('+repr((ROOT/path).read_text(encoding='utf-8'))+',m.__dict__)\n'
        code+='SOURCE_HASHES='+repr(source)+'\nEXPECTED_REPORT_HASHES='+repr(report_hashes)+'\nEXPECTED_MONTHS='+repr(months)+'\nTEST_MODULES='+repr(list(MODULES)[2:])+'\n'+(ROOT/FILES[1]).read_text(encoding='utf-8')
        template=b.corrected_template(job,code);template['containers'][0]['image']=review['image']
        env=template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_ADMIN_URL']
        if len(env)!=1 or not env[0].get('secretRef') or env[0].get('value'):raise ValueError('Existing admin reference only')
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        save(OUT,'intent',{'template':template,'sources':hashes(),'scope':'SELECT-only private QA reconciliation for twenty missing legacy monthly analytics. Preserve frozen NAV/returns/risk, source broker records and all snapshots. No production connection, blob writes, report changes or saved job update.'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Qualification source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    def read(prefix):
        rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
        return [json.loads(r['Result']) for r in rows]
    values=read('LEGACY_MONTHLY_FINAL ' if state=='Succeeded' else 'LEGACY_MONTHLY_ERROR ')
    if len(values)!=1:print('Awaiting reconciliation receipt');return
    proof=values[0]
    if state=='Succeeded':
        items=sorted(read('LEGACY_MONTHLY_ITEM '),key=lambda r:r['period_end'])
        if len(items)!=20:print('Awaiting all month receipts');return
        from ibkr_etl.util import canonical_json
        if not proof['passed'] or proof['native_tests']!=24 or proof['database_writes']!=0 or proof['production_connection_used'] is not False or hashlib.sha256(canonical_json(items).encode()).hexdigest()!=proof['plan_sha256']:raise ValueError('Incomplete reconciliation proof')
        proof['months_detail']=items
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'deployed':False})
    print(json.dumps({'state':state,'proof':{k:v for k,v in proof.items() if k!='months_detail'}}))
if __name__=='__main__':main(*sys.argv[1:])
