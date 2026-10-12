"""One bounded subject/month-history private output qualification at a time."""
import hashlib,json,sys
from pathlib import Path
import control_reporting_image_qa_20261011 as qa
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
ROOT=qa.ROOT;a=qa.a;b=qa.b
FILES=[Path(__file__).name,'probe_reporting_historical_outputs_20261011.py','prepare_reporting_migration_clone_20261011.py','audit_generic_report_numbers.py']
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}
def main(stage,subject):
    if stage not in ('start','read') or subject not in ('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'):raise ValueError('Unknown scope')
    out=qa.OUT.parent/('historical-outputs-'+subject+'-v1')
    baseline=protected(qa.OUT.parent/'resume-v1/result.json');job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['boundary']['workloads'][a.JOB]:raise ValueError('Saved worker drift')
    if stage=='start':
        if out.exists():raise ValueError('Preserve previous historical attempt')
        migration=protected(qa.OUT.parent/'snapshot-migration-v2/result.json');clone=protected(qa.OUT.parent/'migration-clone-v1/result.json')
        review=protected(qa.OUT.parent/'image-review-v1/result.json');plan=protected(qa.OUT.parent/'historical-reconciled-read-v1/result.json')
        raw=(qa.OUT.parent/'historical-reconciled-read-v1/inventory.json').read_bytes()
        if not migration['passed'] or not review['passed'] or hashlib.sha256(raw).hexdigest()!=plan['inventory_sha256']:raise ValueError('Qualified migration and image required')
        expected={r['period_end']:r['candidate_sha256'] for r in json.loads(raw)['rows'] if r['subject']==subject}
        if len(expected)!=21:raise ValueError('Exactly 21 periods required')
        report_hashes={t:clone['proof']['all_tables'][t]['sha256'] for t in ('onboarding_historical_report_items','report_artifact_releases','report_generation_runs','report_release_decisions','reporting_snapshots')}
        report_hashes['reporting_snapshots']=migration['proof']['final_snapshot_fingerprint']['sha256']
        source=qa.release.r.read(qa.release.OUT/'source-intent.json')['candidate_hashes']
        code='import types,sys,os\nsys.path[:0]=["/app/src","/app/scripts"]\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\n'
        for name in ('prepare_reporting_migration_clone_20261011','audit_generic_report_numbers'):
            code+='m=types.ModuleType('+repr(name)+');sys.modules[m.__name__]=m\nexec('+repr((ROOT/'scripts'/(name+'.py')).read_text(encoding='utf-8'))+',m.__dict__)\n'
        code+='SOURCE_HASHES='+repr(source)+'\nEXPECTED_HASHES='+repr(expected)+'\nEXPECTED_REPORT_HASHES='+repr(report_hashes)+'\nSUBJECT='+repr(subject)+'\nIMAGE='+repr(review['image'])+'\n'
        code+=(ROOT/'scripts'/FILES[1]).read_text(encoding='utf-8')
        template=b.corrected_template(job,code);template['containers'][0]['image']=review['image']
        prior=protected(qa.OUT.parent/'image-qa-v1/intent.json')
        template['containers'][0]['env']=prior['template']['containers'][0]['env']
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['boundary']['workloads']:raise ValueError('Workload drift')
        save(out,'intent',{'template':template,'sources':hashes(),'image':review['image'],'expected_periods':expected,
            'scope':'One subject, 21 months, 42 private QA outputs and one receipt. Installed candidate image, read-only migrated QA database, independent six-chart financial audits. No production connection, source overlay, report registration, approvals, saved job update or build-data upload.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(out,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name'],'subject':subject}));return
    intent=protected(out/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Qualification source drift')
    name=protected(out/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='REPORTING_HISTORICAL_FINAL ' if state=='Succeeded' else 'REPORTING_HISTORICAL_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting historical receipt');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (not proof.get('passed') or proof.get('subject')!=subject or proof.get('months')!=21 or proof.get('artifacts')!=42 or proof.get('financial_charts_verified')!=126 or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False):raise ValueError('Incomplete historical qualification')
    label='result' if state=='Succeeded' else 'failure'
    if not (out/(label+'.json')).exists():save(out,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'image':intent['image'],'deployed':False})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
