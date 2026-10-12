"""Execute exact source-only sector integration on the private readonly QA DB."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_corrected_reports_20261011 as base
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
a=base.a;b=base.b;ROOT=base.ROOT
OUT=ROOT/'.work/platform-upgrade-20261011-stage34/sector-integration-v1'
PYTHON={n:'src/ibkr_etl/'+n.rsplit('.',1)[1]+'.py' for n in ('ibkr_etl.reporting_currency_holdings','ibkr_etl.agent_tools','ibkr_etl.reporting_snapshot')}
SOURCE_FILES=[*PYTHON.values(),'src/ibkr_etl/monthly_artifacts.py','src/ibkr_etl/presentation_design.py',*['scripts/'+n for n in ('render_monthly_presentation.mjs','render_alpha_t_presentation.mjs','render_alpha_t_private_presentation.mjs','alpha_t_presentation_runtime.mjs')]]
FILES=[*PYTHON.values(),'scripts/'+Path(__file__).name,'scripts/probe_upgrade_sector_integration_20261011.py','scripts/prepare_upgrade_fresh_reporting_remote_20261011.py']
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage34/resume-v1/result.json')
    job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['boundary']['workloads'][a.JOB]:raise ValueError('Saved worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve earlier attempt')
        snapshots=protected(ROOT/'.work/platform-upgrade-20261011-stage31/fresh-snapshots-v1/result.json')
        expected={s['subject']:s['sha256'] for s in snapshots['proof']['subjects']}
        code='import types,sys,os\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\nq=types.ModuleType("qa_read_only");sys.modules[q.__name__]=q\nexec('+repr((ROOT/FILES[-1]).read_text(encoding='utf-8'))+',q.__dict__)\n'
        code+='EXPECTED_HASHES='+repr(expected)+'\nSOURCE_FILES='+repr(SOURCE_FILES)+'\nCANDIDATES='+repr({n:(ROOT/p).read_text(encoding='utf-8') for n,p in PYTHON.items()})+'\n'+(ROOT/FILES[-2]).read_text(encoding='utf-8')
        template=b.corrected_template(job,code)
        template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name'] in {'DATABASE_URL','AZURE_CLIENT_ID','PYTHONPATH','PATH'}]
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['boundary']['workloads']:raise ValueError('Workload drift')
        save(OUT,'intent',{'template':template,'sources':hashes(),'scope':'Readonly private recovery QA sector and collector comparison for five subjects; nine allowlisted application source files copied to private QA storage, no credentials or database export. No production connection or saved-job updates.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=protected(OUT/'intent.json')
    if hashes()!=intent['sources']:raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution drift')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_SECTOR_FINAL ' if state=='Succeeded' else 'UPGRADE_SECTOR_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting proof ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (proof.get('passed') is not True or len(proof['subjects'])!=5 or proof.get('database_writes')!=0 or not proof.get('qa_state_unchanged') or proof.get('production_connection_used') is not False):raise ValueError('Incomplete proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
