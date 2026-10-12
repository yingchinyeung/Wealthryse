"""Seal corrected QA evidence and live invariants, without claiming deployment."""
import hashlib,json,os,re,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from pypdf import PdfReader
import control_upgrade_main_dns_20261011 as c

OUT=c.ROOT/'.work/platform-upgrade-20261011-stage33'
NODE=Path('C:/Users/antho/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')

def require(ok,message):
    if not ok:raise ValueError(message)

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    require(not (OUT/'closeout.json').exists(),'Preserve prior checkpoint')
    prior=c.old.protected(c.ROOT/'.work/platform-upgrade-20261011-stage32/closeout.json')
    baseline=c.old.protected(c.ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json')
    plan=c.old.protected(c.OUT/'plan.json')
    names=('corrected-reports-v3','corrected-reports-v4','corrected-numbers-v1','v4-comparison-v1','ms-note-v1','ms-note-comparison-v1')
    proofs={n:c.old.protected(OUT/n/'result.json') for n in names}
    require(all(v['passed'] is True for v in proofs.values()),'Incomplete candidate evidence')
    for n in ('corrected-reports-v1','corrected-reports-v2'):
        c.old.protected(OUT/n/'failure.json')
    download_records={n:c.old.protected(OUT/n/'downloads.json') for n in ('artifact-review-v1','artifact-review-v2','artifact-review-ms-note-v1')}
    for manifest in download_records.values():
        require(manifest['passed'] is True,'Download proof failed')
        for row in manifest['artifacts']:
            require(digest(Path(row['file']))==row['sha256'],'Downloaded artifact changed')
    native={
        'hong-kong-china':OUT/'artifact-review-v1/HongKong_China_Corrected_QA_2026-09-30_native.pdf',
        'alpha-world-ms':OUT/'artifact-review-v1/AlphaWorld_MS_Corrected_QA_2026-09-30_native.pdf',
        'alpha-world-t':OUT/'artifact-review-v2/AlphaWorld_T_Corrected_QA_v4_2026-09-30_native.pdf'}
    for path in native.values():require(len(PdfReader(path).pages)==20,'Expected twenty native slides')
    groups=[(['tests.test_reporting_currency_holdings','tests.test_reporting_snapshot','tests.test_presentation_design',
        'tests.test_ms_presentation_model','tests.test_alpha_world_t_presentation_model',
        'scripts.test_upgrade_all_reports_v2_20261011','scripts.test_upgrade_input_lineage_20261011','scripts.test_upgrade_g04_isolation_20261010'],76),
        (['tests.test_monthly_artifacts','tests.test_reporting_currency_regions'],15)]
    env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts','TEMP':str(OUT/'test-temp'),'TMP':str(OUT/'test-temp')}
    counts=[]
    for modules,expected in groups:
        result=subprocess.run([sys.executable,'-m','unittest',*modules,'-q'],cwd=c.ROOT,env=env,capture_output=True,text=True,timeout=180)
        require(result.returncode==0 and re.findall(r'Ran (\d+) tests',result.stderr)==[str(expected)],'Regression test count/result differs: '+result.stderr[-1000:])
        counts.append(expected)
    result=subprocess.run([str(NODE),'--test','--test-reporter=tap','scripts/test_report_content_labels.mjs','scripts/test_report_summary_label.mjs'],cwd=c.ROOT,capture_output=True,text=True,timeout=60)
    require(result.returncode==0 and re.search(r'# pass 10\b',result.stdout) and re.search(r'# fail 0\b',result.stdout),'Label regression tests failed')
    require(c.boundary()==plan['boundary'],'Live configuration drift')
    require(c.old.bindings(c.old.current_resources())==baseline['bindings'],'Workload drift')
    c.links(False)
    health=c.health()
    sources=['src/ibkr_etl/reporting_currency_holdings.py','src/ibkr_etl/reporting_snapshot.py','src/ibkr_etl/presentation_design.py',
        'src/ibkr_etl/monthly_artifacts.py','scripts/render_monthly_presentation.mjs','scripts/report_content_labels.mjs','scripts/report_summary_label.mjs']
    latest=c.old.protected(OUT/'ms-note-v1/intent.json')
    require(latest['sources']['scripts/render_monthly_presentation.mjs']==digest(c.ROOT/'scripts/render_monthly_presentation.mjs'),'Renderer changed since final QA')
    value={'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),
        'previous_checkpoint':prior['protected_receipt'],'evidence':{n:p['protected_receipt'] for n,p in proofs.items()},
        'website':health,'workloads_preserved':37,'public_access_changed':False,'credentials_changed':False,
        'candidate_deployed':False,'production_reports_or_approvals_changed':False,'qa_reports_released':0,
        'current_candidate':'v4 for T and three generic subjects; MS-only note correction supersedes MS v4 presentation',
        'financial_inputs_unchanged_after_layout_fixes':True,'charts_unchanged_in_v4':30,
        'generic_independent_checks':{'subjects':3,'charts':18,'risk_metrics':24,'holding_rows':30,'cash_rows':15,'comparison_rows':15},
        'visual_review':{'factsheets':'All ten v3 pages inspected; the two corrected v4 region pages inspected; MS follow-up factsheet byte-identical',
            'native_full_decks':{s:{'slides_inspected':20,'file':str(p),'sha256':digest(p)} for s,p in native.items()},
            'follow_up_slides':[{'subject':'hong-kong-china','version':'v4','slide':17,'result':'Shortened conclusion fits in native PowerPoint'},
                {'subject':'alpha-world-ms','version':'ms-note-v1','slide':5,'result':'Footnote clears final table row in native PowerPoint; exact geometry-only delta independently verified'}],
            'remaining_full_native_decks':['U16088571','U16088606'],
            'generic_chart_edit_data':'HK performance chart data grid opened successfully; original deck closed without saving and hash preserved'},
        'verified_regressions_this_seal':{'reporting':counts[0],'factsheet_region':counts[1],'node_labels':10},
        'earlier_broad_presentation_suite':{'run':51,'passed':50,'skipped':1},
        'preserved_failed_trials':['corrected-reports-v1: unexpected template container','corrected-reports-v2: validator result shape'],
        'candidate_source_hashes':{n:digest(c.ROOT/n) for n in sources},
        'remaining_gates':['Complete remaining native visual acceptance and qualified-build T/shared-renderer integration',
            'Qualify scoped reporting image: regression, security, runtime and rollback checks; deploy only qualified image',
            'Qualify governed immutable snapshot regeneration and historical replacement without silently changing report approvals',
            'Final private-network cutover with all-caller secret refresh, cold pulls and external denial checks',
            'Coordinated credential rotation with recovery/reconnection and provider-expiry verification',
            'Wider website/download acceptance and genuine whole scheduled cycle'],
        'observation_14_day':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','whole_scheduled_cycle':'UNVERIFIED',
        'human_alert_receipt':'UNVERIFIED','independent_provider_crisis_price_certification':'UNVERIFIED',
        'g04_complete':False,'full_upgrade_complete':False}
    c.old.save(OUT,'closeout',value)
    print(json.dumps({'checkpoint_evidence_verified':True,'tests_passed':sum(counts)+10,'website':health,'full_upgrade_complete':False}))

if __name__=='__main__':main()
