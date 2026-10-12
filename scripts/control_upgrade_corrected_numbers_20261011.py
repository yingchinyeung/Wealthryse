"""Qualify numbers by reading completed QA decks and their frozen source."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_corrected_reports_20261011 as old
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
a=old.a;b=old.b;ROOT=old.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage33/corrected-numbers-v1'
FILES=['scripts/'+Path(__file__).name,'scripts/probe_upgrade_corrected_numbers_20261011.py','scripts/prepare_upgrade_fresh_reporting_remote_20261011.py','scripts/audit_generic_report_numbers.py','src/ibkr_etl/reporting_currency_holdings.py']
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    job=b.request('GET',a.PATH+b.API);baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json')
    if b.bindings([job])[a.JOB]!=baseline['bindings'][a.JOB]:raise ValueError('Worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve prior execution')
        reports=protected(ROOT/'.work/platform-upgrade-20261011-stage33/corrected-reports-v3/result.json')
        if not reports['passed'] or reports['proof']['artifacts']!=10:raise ValueError('Complete candidate output required')
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        code='import types,sys,os\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\nq=types.ModuleType("qa_read_only");sys.modules[q.__name__]=q\nexec('+repr((ROOT/FILES[2]).read_text(encoding='utf-8'))+',q.__dict__)\n'
        sources={'audit_generic_report_numbers':(ROOT/FILES[3]).read_text(encoding='utf-8'),'ibkr_etl.reporting_currency_holdings':(ROOT/FILES[4]).read_text(encoding='utf-8')}
        code+='EXPECTED_OUTPUTS='+repr(reports['proof']['subjects'])+'\nCANDIDATE_SOURCES='+repr(sources)+'\n'+(ROOT/FILES[1]).read_text(encoding='utf-8')
        template=b.corrected_template(job,code);template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name'] in ('DATABASE_URL','AZURE_CLIENT_ID')]
        save(OUT,'intent',{'template':template,'sources':hashes(),'scope':'Read-only QA database and exact three QA decks. No writes/uploads/production connections.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Audit source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution drift')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_CORRECTED_NUMBERS ' if state=='Succeeded' else 'UPGRADE_CORRECTED_NUMBERS_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting audit result ingestion');return
    proof=json.loads(rows[0]['Result']);label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof})
    print(json.dumps(proof))
if __name__=='__main__':main(*sys.argv[1:])
