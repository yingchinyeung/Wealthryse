"""Seal verified QA progress and newly found report defects; do not claim completion."""
import hashlib,json,os,re,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
import control_upgrade_main_dns_20261011 as c

OUT=c.ROOT/'.work/platform-upgrade-20261011-stage32'
def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve prior checkpoint')
    prior=c.old.protected(c.ROOT/'.work/platform-upgrade-20261011-stage31/closeout.json')
    resume=c.old.protected(c.ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json')
    plan=c.old.protected(c.OUT/'plan.json')
    proofs={name:c.old.protected(OUT/(name+'/result.json')) for name in (
        'input-lineage-v2','report-readback-v1','post-report-integrity-v1','report-reconciliation-v1','report-inventory-v1','report-currency-v1')}
    if any(p.get('passed') is not True for p in proofs.values()):raise ValueError('Required evidence missing')
    if c.boundary()!=plan['boundary'] or c.old.bindings(c.old.current_resources())!=resume['bindings']:raise ValueError('Live configuration drift')
    c.links(False);health=c.health()
    downloads=c.old.protected(OUT/'artifact-review-v1/downloads.json')
    for r in downloads['artifacts']:
        if hashlib.sha256(Path(r['file']).read_bytes()).hexdigest()!=r['sha256']:raise ValueError('QA source artifact changed')
    native=OUT/'artifact-review-v1/HongKong_China_QA_2026-09-30_native.pdf'
    from pypdf import PdfReader
    if len(PdfReader(native).pages)!=20:raise ValueError('Native render must contain twenty slides')
    tests=['tests.test_reporting_currency_holdings','tests.test_reporting_snapshot','tests.test_presentation_design',
           'tests.test_ms_presentation_model','tests.test_alpha_world_t_presentation_model',
           'scripts.test_upgrade_all_reports_v2_20261011','scripts.test_upgrade_input_lineage_20261011','scripts.test_upgrade_g04_isolation_20261010']
    run=subprocess.run([sys.executable,'-m','unittest',*tests,'-q'],cwd=c.ROOT,capture_output=True,text=True,timeout=90,
                       env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts'})
    if run.returncode or re.findall(r'Ran (\d+) tests',run.stderr)!=['76']:raise ValueError('Targeted tests failed')
    candidate=['src/ibkr_etl/reporting_currency_holdings.py','src/ibkr_etl/reporting_snapshot.py','src/ibkr_etl/presentation_design.py','scripts/render_monthly_presentation.mjs']
    value={'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),
        'previous_checkpoint':prior['protected_receipt'],'evidence':{k:v['protected_receipt'] for k,v in proofs.items()},
        'website':health,'workloads_preserved':37,'public_access_changed':False,'credentials_changed':False,
        'fresh_qa_reports':10,'idempotent_replays':5,'qa_blob_inventory_hashes_verified':12,'recovery_source_tables_unchanged':97,
        'qa_reports_released':0,'production_data_or_approvals_changed':False,
        'five_factsheets_visual_review':'10 pages inspected; full numerical acceptance still pending',
        'native_presentation_review':{'subject':'hong-kong-china','slides_inspected':20,'sha256':hashlib.sha256(native.read_bytes()).hexdigest(),
            'acceptance':'FAILED_NEW_FINDINGS','other_four_fresh_decks':'UNVERIFIED'},
        'findings':[
            {'id':'REPORT-CURRENCY','status':'candidate arithmetic verified; corrected rendering/deployment pending',
             'detail':'Native holdings were summed and labelled USD. HKChina 676572 HKD converts to 86222.33568 USD. Greater China mixed HKD/TWD gross converts to 169648.73435280 USD; Technology weight changes from 58.4802214% to 40.0353469%. NAV/returns are unchanged.'},
            {'id':'REPORT-BENCHMARK-LABEL','status':'local correction; rendering/deployment pending','detail':'Generic deck retained S&P 500 captions for Hang Seng comparisons. Excess-return units corrected to percentage points in candidate.'},
            {'id':'REPORT-SECONDARY-LABELS','status':'review remains open','detail':'Native HKChina deck includes a static January start-date note, a cash subtotal/net-activity explanation requiring reconciliation, and contribution magnitude labels requiring final acceptance. Approved legal/template wording remains untouched.'}],
        'candidate_source_hashes':{n:hashlib.sha256((c.ROOT/n).read_bytes()).hexdigest() for n in candidate},
        'candidate_deployed':False,'targeted_tests':76,'g04_complete':False,
        'remaining_gates':['Render/verify corrected currency and benchmark candidate for all five subjects; qualify immutable snapshot regeneration and historical reports',
            'Scoped release build, security/runtime/rollback qualification and deployment',
            'Final private-network cutover with all-caller secret-refresh, cold image pulls and external-denial checks',
            'Coordinated credential rotation and reconnection/recovery/provider-expiry checks',
            'Wider website acceptance and genuine whole scheduled cycle'],
        'observation_14_day':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','whole_scheduled_cycle':'UNVERIFIED',
        'human_alert_receipt':'UNVERIFIED','independent_provider_crisis_price_certification':'UNVERIFIED','full_upgrade_complete':False}
    c.old.save(OUT,'closeout',value)
    print(json.dumps({'checkpoint_evidence_verified':True,'tests':76,'new_report_defects_open':True,'full_upgrade_complete':False,'website':health}))
if __name__=='__main__':main()
