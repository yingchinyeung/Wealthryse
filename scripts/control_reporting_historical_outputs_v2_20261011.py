"""All-month installed v4 output checks against versioned private corrections."""
import hashlib,json,sys
from pathlib import Path
import control_reporting_historical_outputs_20261011 as old
import release_reporting_full_nav_20261011 as release
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
ROOT=old.ROOT;a=old.a;b=old.b;STAGE=old.qa.OUT.parent
FILES=[Path(__file__).name,*old.FILES[1:],'audit_monthly_nav_coverage_20261011.py','audit_monthly_nav_coverage_v2_20261011.py']
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}
def main(stage,subject):
    if stage not in ('start','read') or subject not in ('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'):raise ValueError('Unknown historical scope')
    out=STAGE/('historical-outputs-'+subject+'-v2')
    baseline=protected(STAGE/'resume-v1/result.json');job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['boundary']['workloads'][a.JOB]:raise ValueError('Saved reporting job drift')
    if stage=='start':
        if out.exists():raise ValueError('Preserve previous historical attempt')
        migration=protected(STAGE/'monthly-snapshot-enrichment-v1/result.json');clone=protected(STAGE/'migration-clone-v1/result.json')
        review=protected(STAGE/'image-review-v2/result.json');plan=protected(STAGE/'historical-reconciled-read-v1/result.json');monthly=protected(STAGE/'legacy-monthly-plan-v3/result.json')
        raw=(STAGE/'historical-reconciled-read-v1/inventory.json').read_bytes()
        if not all(p['passed'] for p in (migration,clone,review,monthly)) or hashlib.sha256(raw).hexdigest()!=plan['inventory_sha256']:raise ValueError('Qualified migration and reviewed image required')
        expected={r['period_end']:r['candidate_sha256'] for r in json.loads(raw)['rows'] if r['subject']==subject}
        if subject=='U16088571':expected.update({r['period_end']:r['candidate_sha256'] for r in monthly['proof']['months_detail']})
        if len(expected)!=21 or review['image']!=migration['image']:raise ValueError('Exactly 21 periods on qualified image required')
        report_hashes={t:clone['proof']['all_tables'][t]['sha256'] for t in ('onboarding_historical_report_items','report_artifact_releases','report_generation_runs','report_release_decisions','reporting_snapshots')}
        report_hashes['reporting_snapshots']=migration['proof']['final_snapshot_fingerprint']['sha256']
        source=release.r.read(release.OUT/'source-intent.json')['candidate_hashes']
        code='import types,sys,os\nsys.path[:0]=["/app/src","/app/scripts"]\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\n'
        for name in ('prepare_reporting_migration_clone_20261011','audit_generic_report_numbers','audit_monthly_nav_coverage_20261011','audit_monthly_nav_coverage_v2_20261011'):
            code+='m=types.ModuleType('+repr(name)+');sys.modules[m.__name__]=m\nexec('+repr((ROOT/'scripts'/(name+'.py')).read_text())+',m.__dict__)\n'
        code+='SOURCE_HASHES='+repr(source)+'\nEXPECTED_HASHES='+repr(expected)+'\nEXPECTED_REPORT_HASHES='+repr(report_hashes)+'\nSUBJECT='+repr(subject)+'\nIMAGE='+repr(review['image'])+'\n'
        probe=(ROOT/'scripts/probe_reporting_historical_outputs_20261011.py').read_text()
        if probe.count('candidate-stage34-historical-v1/')!=1:raise ValueError('Exact new output prefix required')
        anchor="            work=root/period;work.mkdir();snapshot=work/'snapshot.json';snapshot.write_text(raw,encoding='utf-8')"
        if probe.count(anchor)!=1:raise ValueError('Exact pre-render NAV audit anchor required')
        probe=probe.replace(anchor,"            from audit_monthly_nav_coverage_v2_20261011 import audit as nav_coverage_audit\n            nav_coverage_audit(p)\n"+anchor)
        code+=probe.replace('candidate-stage34-historical-v1/','candidate-stage34-historical-v2/')
        template=b.corrected_template(job,code);template['containers'][0]['image']=review['image']
        template['containers'][0]['env']=protected(STAGE/'image-qa-v2/intent.json')['template']['containers'][0]['env']
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['boundary']['workloads']:raise ValueError('Workload drift')
        save(out,'intent',{'template':template,'sources':hashes(),'image':review['image'],'expected_periods':expected,
            'scope':'21 monthly pairs, installed v4 candidate, read-only private migration QA. Independent financial charts and risk checks; no production connection, registration, approval or saved-job update.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(out,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name'],'subject':subject}));return
    intent=protected(out/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(out/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='REPORTING_HISTORICAL_FINAL ' if state=='Succeeded' else 'REPORTING_HISTORICAL_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting native receipt');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (not proof['passed'] or proof['subject']!=subject or proof['months']!=21 or proof['artifacts']!=42 or proof['financial_charts_verified']!=126 or proof['database_writes']!=0 or proof['production_connection_used'] is not False):raise ValueError('Incomplete historical result')
    label='result' if state=='Succeeded' else 'failure'
    if not (out/(label+'.json')).exists():save(out,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'image':intent['image'],'deployed':False})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
