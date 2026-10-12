"""Read-only candidate arithmetic qualification in the recovery QA database."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_worker_20261010 as a
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
b=a.p;ROOT=b.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage32/report-currency-v1'
FILES=['scripts/probe_upgrade_report_currency_20261011.py','scripts/prepare_upgrade_fresh_reporting_remote_20261011.py','src/ibkr_etl/reporting_currency_holdings.py','scripts/control_upgrade_report_currency_20261011.py']
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json');job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['bindings'][a.JOB]:raise ValueError('Reporting image drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve attempt')
        snapshots=protected(ROOT/'.work/platform-upgrade-20261011-stage31/fresh-snapshots-v1/result.json')
        expected={s['subject']:s['sha256'] for s in snapshots['proof']['subjects']}
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        code='import types,sys,os\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\nq=types.ModuleType("qa_read_only");sys.modules[q.__name__]=q\nexec('+repr((ROOT/FILES[1]).read_text(encoding='utf-8'))+',q.__dict__)\n'
        code+='CANDIDATE_SOURCE='+repr((ROOT/FILES[2]).read_text(encoding='utf-8'))+'\nEXPECTED_HASHES='+repr(expected)+'\n'+(ROOT/FILES[0]).read_text(encoding='utf-8')
        template=b.corrected_template(job,code);template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_URL']
        if len(template['containers'][0]['env'])!=1 or not template['containers'][0]['env'][0].get('secretRef'):raise ValueError('App role reference required')
        save(OUT,'intent',{'template':template,'sources':hashes(),'scope':'Read-only arithmetic correction on three physical/filtered frozen QA inputs. No uploads, source writes, saved snapshots, production connections or deployment.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Candidate drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution drift')
    execution_env(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    if state=='Failed':raise ValueError('Candidate arithmetic failed; preserve and inspect')
    prefix='UPGRADE_REPORT_CURRENCY '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting proof');return
    proof=json.loads(rows[0]['Result'])
    if not proof.get('passed') or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False or len(proof.get('subjects',[]))!=3:raise ValueError('Incomplete arithmetic/isolation proof')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof})
    print(json.dumps(proof))
if __name__=='__main__':main(*sys.argv[1:])
