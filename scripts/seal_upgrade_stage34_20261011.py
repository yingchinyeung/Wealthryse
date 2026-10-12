"""Protect a partial reporting checkpoint; never declare the full upgrade done."""
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import control_upgrade_main_dns_20261011 as c
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage34'
EVIDENCE=(
    'factsheet-history-outputs-v1/result.json',
    'factsheet-history-review-v1/visual-review.json',
    'all-monthly-nav-audit-v3/result.json',
    'historical-outputs-alpha-world-ms-v3/result.json',
    'historical-outputs-alpha-world-t-v3/result.json',
    'release-policy-v3/result.json',
)


def main():
    if (OUT/'closeout.json').exists(): raise ValueError('Preserve prior checkpoint')
    with ThreadPoolExecutor(max_workers=4) as pool:
        proofs=dict(zip(EVIDENCE,pool.map(lambda n:protected(OUT/n),EVIDENCE)))
    if any(v.get('passed') is not True for v in proofs.values()): raise ValueError('Failed evidence')
    runtime_failure=protected(OUT/'consumer-agent-web-v3/runtime-v5/failure.json')
    if runtime_failure.get('passed') is not False: raise ValueError('Expected preserved unresolved runtime finding')
    history=proofs[EVIDENCE[0]]['proof'];nav=proofs[EVIDENCE[2]]['proof']
    if (history['factsheets'],history['history_rows_verified'],nav['months_checked'],nav['nav_intervals_verified'],nav['failed_months'])!=(63,756,105,2208,0):
        raise ValueError('Reporting coverage differs')
    scans={}
    for name in ('reporting-image-v5','consumer-agent-web-v3','consumer-agent-assurance-v3'):
        scan=json.loads((OUT/name/'scan-completed.json').read_bytes())
        remote,_=restore_protected(json.loads((OUT/name/'scan-protected-receipt.json').read_bytes()))
        if scan!=remote or scan['assessment']['status']!='pass': raise ValueError('Scan evidence differs')
        scans[name]={'image':scan['image'],'counts':scan['assessment']['counts']}
    env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts','TEMP':str(ROOT/'.work/platform-upgrade-20261011-stage33/test-temp'),'TMP':str(ROOT/'.work/platform-upgrade-20261011-stage33/test-temp')}
    run=subprocess.run([sys.executable,'-m','unittest','scripts.test_reporting_agent_consumer_patch_20261011','scripts.test_reporting_composite_runtime_reference_20261011','tests.test_factsheet_portfolio_history','tests.test_monthly_artifacts','-q'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=180)
    if run.returncode or re.findall(r'Ran (\d+) tests',run.stderr)!=['36']: raise ValueError('Final focused regressions failed')
    plan=protected(c.OUT/'plan.json')
    if c.boundary()!=plan['boundary']: raise ValueError('Production boundary drift')
    c.links(False)
    health=c.health()
    value={
        'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),
        'evidence':{n:p['protected_receipt'] for n,p in proofs.items()},'scans':scans,
        'financial_qa':{'monthly_snapshots':105,'nav_intervals':2208,'corrected_generic_factsheets':63,'history_rows':756},
        'final_focused_tests_passed':36,'website':health,'workloads_preserved':37,
        'production_candidate_deployed':False,'production_reports_or_approvals_changed':False,
        'broker_data_changed':False,'private_network_cutover_complete':False,'credentials_rotated':False,
        'consumer_runtime_acceptance':'BLOCKED: reconcile current instrument symbols against frozen classification labels before promoting either consumer',
        'runtime_failure':runtime_failure['protected_receipt'],
        'runtime_reconciliation_notes':['T report collector uses different labels and metadata from website sector tool. Composite application branch is unchanged; amounts and weights independently reconcile to source components.',
            'Exact per-security comparison identified symbol differences, not merely list ordering. This remains an unresolved qualification finding; failed trials are retained.'],
        'remaining_gates':['Reconcile consumer runtime symbol finding and pass website plus assurance installed-image private checks',
            'Qualify older snapshot, historical, manual presentation and onboarding-render consumers against their own source baselines',
            'Complete governed production snapshot/report replacement and approval/download handover; do not transfer old approvals silently',
            'Complete historical visual acceptance beyond reviewed samples',
            'Deploy qualified consumer-specific images and verify installed runtime, rollback and downloads',
            'Complete private-network caller, secret refresh, cold-pull and external-denial acceptance',
            'Rotate credentials with verified recovery and reconnection',
            'Complete wider website acceptance and a genuine full scheduled cycle'],
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED',
        'whole_scheduled_cycle':'UNVERIFIED','human_alert_receipt':'UNVERIFIED',
        'independent_crisis_price_provider_certification':'UNVERIFIED','full_historical_visual_acceptance':'UNVERIFIED',
        'full_upgrade_complete':False,
    }
    save(OUT,'closeout',value)
    print(json.dumps({'checkpoint_evidence_verified':True,'final_focused_tests_passed':36,'website':health,'full_upgrade_complete':False}))


if __name__=='__main__':main()
