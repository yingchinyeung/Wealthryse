"""Run bounded, read-only five-subject replay and QA-container integrity checks."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_all_reports_v2_20261011 as a
b=a.b;ROOT=a.ROOT;save=a.save;protected=a.protected
OUT=ROOT/'.work/platform-upgrade-20261011-stage32/report-inventory-v1'

def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'probe_upgrade_report_inventory_20261011.py','probe_upgrade_g04_worker_20261010.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json');job=b.p.request('GET',b.PATH+b.p.API)
    if b.p.bindings([job])[b.JOB]!=baseline['bindings'][b.JOB]:raise ValueError('Worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve previous attempt')
        prior=protected(ROOT/'.work/platform-upgrade-20261010-stage29/fresh-report-v1/result.json')['proof']['artifacts']
        fresh=protected(ROOT/'.work/platform-upgrade-20261011-stage32/report-readback-v1/result.json')['proof']['subjects']
        reconciled=protected(ROOT/'.work/platform-upgrade-20261011-stage32/report-reconciliation-v1/result.json')
        if not reconciled['passed']:raise ValueError('Five receipts required')
        expected={r['output_reference'].split('/',1)[1]:r['sha256'] for r in prior}
        for s in fresh:
            for r in s['artifacts']:expected[r['output_reference'].split('/',1)[1]]=r['sha256']
        if len(expected)!=12:raise ValueError('Expected twelve distinct immutable outputs')
        b.p.runtime.safe_window();b.p.runtime.idle(b.JOB)
        code='import types,sys\nq=types.ModuleType("qa_boundary");sys.modules[q.__name__]=q\nexec('+repr((ROOT/'scripts/probe_upgrade_g04_worker_20261010.py').read_text(encoding='utf-8'))+',q.__dict__)\n'
        code+='EXPECTED_WORKER_SHA='+repr(hashlib.sha256(b.WORKER.read_text(encoding='utf-8').encode()).hexdigest())+'\nEXPECTED_BLOBS='+repr(expected)+'\n'
        code+=(ROOT/'scripts/probe_upgrade_report_inventory_20261011.py').read_text(encoding='utf-8')
        template=b.p.corrected_template(job,code)
        save(OUT,'intent',{'template':template,'sources':hashes(),'expected_blobs':expected,'binding':b.p.bindings([job]),'scope':'Read-only isolated QA replay and inventory. Both upload bindings prohibited; no generation, production connection or approvals.'})
        ack=b.p.request('POST',b.PATH+'/start'+b.p.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.p.request('GET',b.PATH+'/executions/'+name+b.p.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution drift')
    a.exact_environment(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    if state=='Failed':raise ValueError('Read-only inventory failed; preserve and inspect without regeneration')
    prefix='UPGRADE_REPORT_INVENTORY '
    rows=b.p.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting proof');return
    proof=json.loads(rows[0]['Result'])
    if not proof.get('passed') or proof.get('database_writes')!=0 or proof.get('uploads_allowed') is not False or proof.get('production_connection_used') is not False or len(proof.get('replayed_subjects',[]))!=5:raise ValueError('Incomplete isolation')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof})
    print(json.dumps(proof))

if __name__=='__main__':main(*sys.argv[1:])
