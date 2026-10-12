"""Private candidate rendering without changing saved jobs or QA report state."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_worker_20261010 as a
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
b=a.p;ROOT=b.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage33/corrected-reports-v1'
PYTHON={'ibkr_etl.reporting_currency_holdings':'src/ibkr_etl/reporting_currency_holdings.py','ibkr_etl.presentation_design':'src/ibkr_etl/presentation_design.py'}
JS=['render_monthly_presentation.mjs','report_content_labels.mjs']
FILES=['scripts/'+Path(__file__).name,'scripts/probe_upgrade_corrected_reports_20261011.py','scripts/prepare_upgrade_fresh_reporting_remote_20261011.py',*PYTHON.values(),*['scripts/'+n for n in JS]]
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json');job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['bindings'][a.JOB]:raise ValueError('Saved reporting binding drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve attempt and reconcile first')
        protected(ROOT/'.work/platform-upgrade-20261011-stage32/closeout.json')
        snapshots=protected(ROOT/'.work/platform-upgrade-20261011-stage31/fresh-snapshots-v1/result.json')
        expected={s['subject']:s['sha256'] for s in snapshots['proof']['subjects']}
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['bindings']:raise ValueError('Live workload drift')
        code='import types,sys,os\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\nq=types.ModuleType("qa_read_only");sys.modules[q.__name__]=q\nexec('+repr((ROOT/FILES[2]).read_text(encoding='utf-8'))+',q.__dict__)\n'
        code+='EXPECTED_HASHES='+repr(expected)+'\nPYTHON_CANDIDATES='+repr({k:(ROOT/v).read_text(encoding='utf-8') for k,v in PYTHON.items()})+'\nJS_CANDIDATES='+repr({n:(ROOT/'scripts'/n).read_text(encoding='utf-8') for n in JS})+'\n'+(ROOT/FILES[1]).read_text(encoding='utf-8')
        template=b.corrected_template(job,code)
        allowed={'DATABASE_URL','AZURE_STORAGE_ACCOUNT_URL','AZURE_CLIENT_ID','PRESENTATION_TEMPLATE_PATH','PRESENTATION_TEMPLATE_SHA256','NODE_EXECUTABLE','PYTHONPATH','RUNTIME_PYTHON','PYTHON_BINARY','PATH'}
        template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name'] in allowed]
        save(OUT,'intent',{'template':template,'sources':hashes(),'scope':'Read-only private East Asia recovery QA inputs; ten candidate outputs under QA candidate-stage33-v1 only. No registration, approvals, production connection, saved job change, build-data upload or publication.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Candidate source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution drift')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_CORRECTED_FINAL ' if state=='Succeeded' else 'UPGRADE_CORRECTED_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting proof ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state=='Failed':
        if not (OUT/'failure.json').exists():save(OUT,'failure',{'execution':name,'proof':proof})
        print(json.dumps({'failed':True,'proof':proof}));return
    if proof.get('passed') is not True or proof.get('artifacts')!=10 or proof.get('database_writes')!=0 or proof.get('registered_reports')!=0 or not proof.get('qa_state_unchanged') or proof.get('production_connection_used') is not False:raise ValueError('Incomplete isolation evidence')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof,'full_upgrade_complete':False})
    print(json.dumps({'passed':True,'artifacts':10,'deployed':False,'visual_acceptance':'UNVERIFIED'}))
if __name__=='__main__':main(*sys.argv[1:])
