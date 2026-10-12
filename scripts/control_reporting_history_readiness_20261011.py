"""Inspect all historical monthly analytics without rendering or mutating them."""
import hashlib,json,sys
from pathlib import Path
import control_reporting_historical_outputs_20261011 as base
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
ROOT=base.ROOT;a=base.a;b=base.b;OUT=base.qa.OUT.parent/'historical-readiness-v1'
FILES=[Path(__file__).name,'probe_reporting_history_readiness_20261011.py','prepare_reporting_migration_clone_20261011.py']
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    baseline=protected(OUT.parent/'resume-v1/result.json');job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['boundary']['workloads'][a.JOB]:raise ValueError('Saved worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve previous readiness check')
        failed=protected(OUT.parent/'historical-outputs-U16088571-v1/failure.json')
        if failed['proof']['stage']!='presentation:2025-01-31':raise ValueError('Unexpected historical failure')
        previous=protected(OUT.parent/'historical-outputs-U16088571-v1/intent.json')
        migration=protected(OUT.parent/'snapshot-migration-v2/result.json');clone=protected(OUT.parent/'migration-clone-v1/result.json')
        source=base.qa.release.r.read(base.qa.release.OUT/'source-intent.json')['candidate_hashes']
        report_hashes={t:clone['proof']['all_tables'][t]['sha256'] for t in ('onboarding_historical_report_items','report_artifact_releases','report_generation_runs','report_release_decisions','reporting_snapshots')}
        report_hashes['reporting_snapshots']=migration['proof']['final_snapshot_fingerprint']['sha256']
        helper=(ROOT/'scripts'/FILES[2]).read_text(encoding='utf-8')
        code='import types,sys,os\nsys.path[:0]=["/app/src","/app/scripts"]\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\nm=types.ModuleType("prepare_reporting_migration_clone_20261011");sys.modules[m.__name__]=m\nexec('+repr(helper)+',m.__dict__)\n'
        code+='SOURCE_HASHES='+repr(source)+'\nEXPECTED_REPORT_HASHES='+repr(report_hashes)+'\n'+(ROOT/'scripts'/FILES[1]).read_text(encoding='utf-8')
        template=b.corrected_template(job,code);template['containers'][0]['image']=previous['image']
        template['containers'][0]['env']=[e for e in previous['template']['containers'][0]['env'] if e['name'] in ('DATABASE_URL','PYTHONPATH','PATH')]
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        save(OUT,'intent',{'template':template,'sources':hashes(),'scope':'SELECT-only readiness for 105 QA snapshots and deterministic feasibility calculation for missing monthly analytics; no blob or DB writes, source mutation, publication or production connection.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Readiness source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Readiness execution differs')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='REPORTING_READINESS_FINAL ' if state=='Succeeded' else 'REPORTING_READINESS_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting readiness receipt');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (not proof.get('passed') or proof.get('snapshots')!=105 or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False):raise ValueError('Incomplete readiness proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'deployed':False})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
