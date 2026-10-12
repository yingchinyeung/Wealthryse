"""Exact onboarding image: private first/latest monthly pairs for all five subjects."""
import hashlib,json,sys
from pathlib import Path
import control_reporting_historical_outputs_20261011 as old
import release_reporting_onboard_20261011 as release
import scan_ms_presentation_worker_candidate as scan
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
ROOT=old.ROOT;a=old.a;b=old.b
STAGE=ROOT/'.work/platform-upgrade-20261011-stage36';PRIOR=ROOT/'.work/platform-upgrade-20261011-stage34';OUT=STAGE/'onboard-runtime-v1'
SUBJECTS=('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t')
FILES=(Path(__file__).name,'probe_reporting_historical_outputs_20261011.py','prepare_reporting_migration_clone_20261011.py','audit_generic_report_numbers.py','audit_monthly_nav_coverage_20261011.py','audit_monthly_nav_coverage_v2_20261011.py')

def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}

def probe_source():
    source=(ROOT/'scripts'/FILES[1]).read_text(encoding='utf-8')
    anchor="            work=root/period;work.mkdir();snapshot=work/'snapshot.json';snapshot.write_text(raw,encoding='utf-8')"
    changes={
        'candidate-stage34-historical-v1/':'candidate-stage36-onboard-v1/',
        '        strategy=None':"        rows=[r for r in rows if r['period_end'] in ('2025-01-31','2026-09-30')]\n        if len(rows)!=2:raise ValueError('First and latest periods required')\n        strategy=None",
        anchor:"            from audit_monthly_nav_coverage_v2_20261011 import audit as nav_coverage_audit\n            nav_coverage_audit(p)\n"+anchor,
        "'months':21,'artifacts':42,'slides_structurally_checked':420,'financial_charts_verified':126":"'months':2,'artifacts':4,'slides_structurally_checked':40,'financial_charts_verified':12",
    }
    for before,after in changes.items():
        if source.count(before)!=1:raise ValueError('Exact QA anchor required: '+before)
        source=source.replace(before,after)
    return source

def accept(proofs):
    if len(proofs)!=5 or {p.get('subject') for p in proofs}!=set(SUBJECTS):raise ValueError('All five subjects required')
    for p in proofs:
        if p.get('passed') is not True or any(type(p.get(k)) is not int or p[k]!=v for k,v in {'months':2,'artifacts':4,'slides_structurally_checked':40,'financial_charts_verified':12,'database_writes':0,'report_registrations':0}.items()):raise ValueError('Incomplete numeric/output qualification')
        if p.get('production_connection_used') is not False or p.get('approvals_changed') is not False:raise ValueError('Private boundary violated')
        if p.get('native_visual_review')!='UNVERIFIED' or p.get('real_viewer_sign_in')!='UNVERIFIED' or p.get('fourteen_day_observation')!='UNVERIFIED':raise ValueError('Unperformed acceptance overstated')
        if p.get('receipt_blob')!='candidate-stage36-onboard-v1/'+p['subject']+'/receipt.json':raise ValueError('Unexpected receipt scope')

def main(stage):
    if stage not in ('start','read'):raise ValueError('Start or read only')
    baseline=protected(STAGE/'resume-v1/result.json');job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['boundary']['workloads'][a.JOB]:raise ValueError('Saved reporting job drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve earlier trial; reconcile before retry')
        candidate=protected(STAGE/'candidate-checkpoint.json');source,_,build=scan.verify(release.OUT)
        image=build['registry']+'/'+build['repository']+'@'+build['digest']
        if not candidate['passed'] or candidate['image']!=image or candidate['native_tests']!=104 or any(candidate['scan_counts'][k] for k in ('CRITICAL','HIGH','UNKNOWN')):raise ValueError('Qualified exact candidate required')
        migration=protected(PRIOR/'august-snapshot-correction-v1/result.json');clone=protected(PRIOR/'migration-clone-v1/result.json')
        plan=protected(PRIOR/'historical-reconciled-read-v1/result.json');monthly=protected(PRIOR/'legacy-monthly-plan-v3/result.json');august=protected(PRIOR/'august-monthly-plan-v1/result.json')
        raw=(PRIOR/'historical-reconciled-read-v1/inventory.json').read_bytes()
        if not all(p['passed'] for p in (migration,clone,monthly,august)) or hashlib.sha256(raw).hexdigest()!=plan['inventory_sha256']:raise ValueError('Qualified private correction required')
        report_hashes={t:clone['proof']['all_tables'][t]['sha256'] for t in ('onboarding_historical_report_items','report_artifact_releases','report_generation_runs','report_release_decisions','reporting_snapshots')}
        report_hashes['reporting_snapshots']=migration['proof']['final_snapshot_fingerprint']['sha256']
        code='import types,sys,os\nsys.path[:0]=["/app/src","/app/scripts"]\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\n'
        for name in FILES[2:]:
            module=Path(name).stem
            code+='m=types.ModuleType('+repr(module)+');sys.modules[m.__name__]=m\nexec('+repr((ROOT/'scripts'/name).read_text(encoding='utf-8'))+',m.__dict__)\n'
        expected_all={}
        for subject in SUBJECTS:
            expected={r['period_end']:r['candidate_sha256'] for r in json.loads(raw)['rows'] if r['subject']==subject}
            if subject=='U16088571':expected.update({r['period_end']:r['candidate_sha256'] for r in monthly['proof']['months_detail']})
            if subject=='U16088606':expected['2026-08-31']=august['proof']['candidate_sha256']
            if len(expected)!=21:raise ValueError('All 21 frozen inputs must be hash-bound')
            expected_all[subject]=expected
            values={'__name__':'__main__','SOURCE_HASHES':source['candidate_hashes'],'EXPECTED_HASHES':expected,'EXPECTED_REPORT_HASHES':report_hashes,'SUBJECT':subject,'IMAGE':image}
            code+='exec('+repr(probe_source())+','+repr(values)+')\n'
        template=b.corrected_template(job,code);template['containers'][0]['image']=image
        template['containers'][0]['env']=protected(PRIOR/'image-qa-v2/intent.json')['template']['containers'][0]['env']
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['boundary']['workloads']:raise ValueError('Workload drift')
        save(OUT,'intent',{'template':template,'sources':hashes(),'image':image,'expected_periods':expected_all,'scope':'Ten first/latest monthly pairs across five subjects, exact onboarding candidate. SELECT-only private migration database; no production connection, registration, approval, saved-job change or build-service data upload.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Frozen QA sources changed')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Runtime differs')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='REPORTING_HISTORICAL_FINAL ' if state=='Succeeded' else 'REPORTING_HISTORICAL_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=(5 if state=='Succeeded' else 1):print('Awaiting complete native receipts');return
    proofs=[json.loads(r['Result']) for r in rows]
    if state=='Succeeded':accept(proofs)
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','image':intent['image'],'execution':name,'proofs':proofs,'deployed':False,'real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED'})
    print(json.dumps({'state':state,'proofs':proofs}))

if __name__=='__main__':main(*sys.argv[1:])
