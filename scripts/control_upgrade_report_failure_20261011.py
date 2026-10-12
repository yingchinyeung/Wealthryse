"""Read-only reproduction of the guarded first-subject failure; no uploads."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_all_reports_20261011 as a
b=a.b;ROOT=a.ROOT;save=a.save;protected=a.protected
OUT=ROOT/'.work/platform-upgrade-20261011-stage32/report-failure-v1'
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'probe_upgrade_report_failure_20261011.py','probe_upgrade_g04_worker_20261010.py')}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json');job=b.p.request('GET',b.PATH+b.p.API)
    if b.p.bindings([job])[b.JOB]!=baseline['bindings'][b.JOB]:raise ValueError('Worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve diagnosis attempt')
        prior=protected(a.OUT/'failure.json')
        if prior['proof']['stage']!='generate:U16088571':raise ValueError('Different failure scope')
        b.p.runtime.safe_window();b.p.runtime.idle(b.JOB)
        code='import types,sys\nq=types.ModuleType("qa_boundary");sys.modules[q.__name__]=q\nexec('+repr((ROOT/'scripts/probe_upgrade_g04_worker_20261010.py').read_text(encoding='utf-8'))+',q.__dict__)\n'
        code+='EXPECTED_WORKER_SHA='+repr(hashlib.sha256(b.WORKER.read_text(encoding='utf-8').encode()).hexdigest())+'\n'+(ROOT/'scripts/probe_upgrade_report_failure_20261011.py').read_text(encoding='utf-8')
        template=b.p.corrected_template(job,code)
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.p.bindings([job]),'scope':'Read-only QA failure diagnosis; private temporary render if reached; all uploads disabled; no report registrations or approval changes'})
        ack=b.p.request('POST',b.PATH+'/start'+b.p.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.p.request('GET',b.PATH+'/executions/'+name+b.p.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution drift')
    a.exact_environment(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    if state=='Failed':raise ValueError('Diagnostic failed before result; preserve')
    prefix='UPGRADE_REPORT_FAILURE_DIAG '
    rows=b.p.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting diagnosis');return
    proof=json.loads(rows[0]['Result'])
    if proof.get('passed') is not True or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False or proof.get('uploads_allowed') is not False:raise ValueError('Isolation failure')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof})
    print(json.dumps(proof))
if __name__=='__main__':main(*sys.argv[1:])
