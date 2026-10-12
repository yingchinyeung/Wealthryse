"""Read-only historical migration sizing; no corrections are committed."""
import hashlib,json,sys
from pathlib import Path
import control_reporting_package_compat_20261011 as base
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
qa=base.qa;ROOT=qa.ROOT;a=qa.a;b=qa.b;OUT=qa.OUT.parent/'historical-plan-v1'
FILES=['scripts/'+Path(__file__).name,'scripts/probe_reporting_history_20261011.py','scripts/prepare_upgrade_fresh_reporting_remote_20261011.py']
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    baseline=protected(OUT.parent/'resume-v1/result.json');job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['boundary']['workloads'][a.JOB]:raise ValueError('Saved worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve earlier trial')
        review=protected(OUT.parent/'image-review-v1/result.json');failure=protected(OUT.parent/'package-candidate-v1/failure.json')
        if not review['passed'] or failure['proof']['message']!='Existing package requires controlled regeneration: U16088571':raise ValueError('Unexpected prior acceptance state')
        helper=(ROOT/FILES[2]).read_text(encoding='utf-8')
        if helper.count("TEST_DB='upgrade_g04_fresh_20261011'")!=1:raise ValueError('QA helper changed')
        helper=helper.replace("TEST_DB='upgrade_g04_fresh_20261011'","TEST_DB='upgrade_g04_20261010'")
        source=qa.release.r.read(qa.release.OUT/'source-intent.json')['candidate_hashes']
        code='import types,sys,os\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\nq=types.ModuleType("qa_read_only");sys.modules[q.__name__]=q\nexec('+repr(helper)+',q.__dict__)\nSOURCE_HASHES='+repr(source)+'\n'+(ROOT/FILES[1]).read_text(encoding='utf-8')
        template=b.corrected_template(job,code);template['containers'][0]['image']=review['image']
        template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name'] in {'DATABASE_URL','AZURE_CLIENT_ID','PYTHONPATH','PATH'}]
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['boundary']['workloads']:raise ValueError('Workload drift')
        save(OUT,'intent',{'template':template,'sources':hashes(),'scope':'Read-only private QA historical snapshot correction hashes and existing report metadata. One private inventory blob only. No DB writes, report generation, production connection or saved job change.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Probe changed')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='REPORTING_HISTORY_FINAL ' if state=='Succeeded' else 'REPORTING_HISTORY_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting receipt ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (not proof.get('passed') or not proof.get('qa_report_state_unchanged') or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False or proof.get('migration_executed') is not False):raise ValueError('Incomplete isolation proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'deployed':False})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
