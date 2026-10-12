"""Website-role candidate/fallback trials; never changes the saved inbox job."""
import calendar,json,re,sys
from datetime import date
from pathlib import Path
import build_upgrade_model_cash_20261010 as b
import scan_ms_presentation_worker_candidate as scan
import control_upgrade_workflow_runtime_20261010 as c
from control_upgrade_readers_20261010 import execution_env
from rehearse_ms_worker_image_rollback import restore_protected

OUT=b.OUT/'runtime-v1';JOB='ibkrv2stg-inbox-acceptance-r2';PATH=c.rid('Microsoft.App/jobs',JOB);API='?api-version=2026-07-01'


def candidate():
    source,_,output=scan.verify(b.OUT);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    record=b.h.read(b.OUT/'scan-completed.json');remote,_=restore_protected(b.h.read(b.OUT/'scan-protected-receipt.json'))
    if record!=remote or record['image']!=image or record['assessment']['status']!='pass' or record['assessment']['blocking_findings']:raise ValueError('Exact protected security scan required')
    for name in ('source-intent','local-qualification','native-build-qualified'):
        archived,_=restore_protected(record['protected_artifact_receipts'][name]);raw=b.h.read(b.OUT/(name+'.json'))
        if archived['content']!=raw or archived['sha256']!=b.h.sha(b.OUT/(name+'.json')):raise ValueError('Candidate evidence changed')
    local=b.h.read(b.OUT/'local-qualification.json');native=b.h.read(b.OUT/'native-build-qualified.json')
    if local['tests']!=29 or local.get('passed') is not True or native['linux_tests']!=300:raise ValueError('Qualification incomplete')
    return image,source


def log_day(value):
    # Log Analytics parse_json converts ISO dates to UTC datetime strings.
    # Accept only the exact date or its lossless midnight-UTC representation.
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}(?:T00:00:00(?:\.0{1,7})?Z)?',value):raise ValueError('Ambiguous telemetry date')
    return date.fromisoformat(value[:10]).isoformat()


def accept(result,mode):
    if result.get('passed') is not True or result.get('mode')!=mode or result.get('tests')!=(14 if mode=='fallback' else 42) or result.get('source_files')!=files(mode):raise ValueError('Exact runtime proof required')
    rows=result.get('checked_model_months',[])
    if len(rows)!=(0 if mode=='fallback' else 42) or any(r.get('passed') is not True for r in rows):raise ValueError('Monthly coverage incomplete')
    if mode!='fallback':
        expected={(s,f'{y}-{month:02d}-{calendar.monthrange(y,month)[1]}') for s in ('alpha-world-ms','alpha-world-t') for y,months in ((2025,range(1,13)),(2026,range(1,10))) for month in months}
        if {(r.get('subject'),log_day(r.get('requested_month'))) for r in rows}!=expected:raise ValueError('Monthly coverage duplicated or changed')
        if any(log_day(r.get('as_of'))>log_day(r['requested_month']) for r in rows):raise ValueError('Future-dated cash evidence')
    if result.get('readiness_statuses')!=[200,503,200] or result.get('verified_tls') is not True or result.get('database_readonly') is not True or result.get('database_role')!='ibkr_stg_web' or type(result.get('database_writes')) is not int or result['database_writes']!=0 or result.get('reports_changed') is not False:raise ValueError('Read-only readiness boundary failed')


def files(mode):
    if mode=='fallback':return {'src/ibkr_etl/web.py':b.WEB_SHA}
    return {p:b.h.sha(b.OUT/'context'/p) for p in ('src/ibkr_etl/web.py','src/ibkr_etl/model_cash_display.py','ALPHA_T_RUNTIME_RELEASE.json')}


def main(stage,mode='candidate'):
    if mode not in ('candidate','fallback','installed'):raise ValueError('Unknown mode')
    image,_=candidate();job=c.request('GET',PATH+API)
    installed=c.protected(b.ROOT/'.work/platform-upgrade-20261010-stage25/inboxr2-runtime-v1/deployment-v1/verified.json')
    if job['properties']['template']['containers'][0]['image']!=installed['image']:raise ValueError('Inbox job changed')
    if stage=='freeze':
        c.save(OUT,'baseline',{'job':job,'invariant':c.settings_invariant(job),'image':installed['image']});return
    baseline=c.protected(OUT/'baseline.json');folder=OUT/mode
    if c.settings_invariant(job)!=baseline['invariant']:raise ValueError('Inbox settings changed')
    if stage=='start':
        c.runtime.safe_window();c.runtime.idle(JOB)
        if folder.exists():raise ValueError('Reconcile existing trial')
        tests={p.stem:p.read_text(encoding='utf-8') for p in (b.OUT/'context/tests').glob('test_*.py')}
        code='MODE='+repr(mode)+'\nEXPECTED_FILES='+repr(files(mode))+'\nTEST_SOURCES='+repr(tests)+'\n'+(b.ROOT/'scripts/probe_upgrade_model_cash_20261010.py').read_text(encoding='utf-8')
        template=c.corrected_template(job,code);container=template['containers'][0];container['image']=b.IMAGE if mode=='fallback' else image
        container['env']=[e for e in container.get('env',[]) if e['name'] in ('DATABASE_URL','PORTFOLIO_TENANT_ID')]
        secrets=[e for e in container['env'] if e['name']=='DATABASE_URL']
        if len(secrets)!=1 or not secrets[0].get('secretRef') or secrets[0].get('value'):raise ValueError('Existing web-role database reference required')
        c.save(folder,'intent',{'image':container['image'],'template':template,'baseline_receipt':baseline['protected_receipt'],'scope':'SELECT-only model cash checks, synthetic access tests and readiness; no normal inbox workflow'})
        ack=c.request('POST',PATH+'/start'+API,data=template);c.save(folder,'dispatch',{'execution':ack['name']});print(json.dumps({'mode':mode,'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=c.protected(folder/'intent.json');name=c.protected(folder/'dispatch.json')['execution'];run=c.request('GET',PATH+'/executions/'+name+API);actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Actual execution differs')
    actual_env=actual.get('env') or [];wanted_env=wanted['env']
    if [e for e in actual_env if e['name']!='DATABASE_URL']!=[e for e in wanted_env if e['name']!='DATABASE_URL']:raise ValueError('Nonsecret execution environment differs')
    execution_env(JOB,[e for e in actual_env if e['name']=='DATABASE_URL'],[e for e in wanted_env if e['name']=='DATABASE_URL']);state=run['properties']['status'];print(json.dumps({'mode':mode,'status':state}))
    if state not in ('Succeeded','Failed'):return
    if state=='Failed':
        if not (folder/'failure.json').exists():c.save(folder,'failure',{'passed':False,'execution':name})
        raise ValueError('Cash runtime trial failed; no deployment')
    prefix='MODEL_CASH_RUNTIME ';q="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))'
    rows=c.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',q)
    if len(rows)!=1:print('Awaiting proof ingestion');return
    proof=json.loads(rows[0]['Result']);accept(proof,mode)
    current=c.request('GET',PATH+API)
    if c.settings_invariant(current)!=baseline['invariant'] or current['properties']['template']['containers'][0]['image']!=baseline['image']:raise ValueError('Saved job drift')
    if not (folder/'result.json').exists():c.save(folder,'result',{'passed':True,'image':intent['image'],'execution':name,'proof':proof,'saved_job_unchanged':True})
    print(json.dumps({'mode':mode,'passed':True,'model_months':len(proof['checked_model_months'])}))


if __name__=='__main__':main(*sys.argv[1:])
