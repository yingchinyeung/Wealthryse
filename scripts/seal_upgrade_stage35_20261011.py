"""Seal verified sector deployments; keep outstanding full-upgrade gates explicit."""
import hashlib,json,os,re,subprocess,sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import control_upgrade_main_dns_20261011 as c
import deploy_reporting_sector_assurance_20261011 as a

ROOT=Path(__file__).resolve().parents[1];OUT=a.w.STAGE
EVIDENCE=('symbol-audit-v1/result.json','consumer-web-runtime-v6/result.json',
 'consumer-assurance-runtime-v6/result.json','web-deployment-v1/verified.json',
 'web-installed-role-v1/result.json','consumer-assurance-cache-v1/result.json',
 'consumer-assurance-full-v2/result.json','assurance-deployment-v1/verified.json',
 'assurance-installed-role-v1/result.json')

def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve prior checkpoint')
    with ThreadPoolExecutor(max_workers=4) as pool:
        proofs=dict(zip(EVIDENCE,pool.map(lambda n:a.protected(OUT/n),EVIDENCE)))
    if any(p.get('passed') is not True for p in proofs.values()):raise ValueError('Incomplete acceptance')
    plan=a.protected(a.OUT/'plan.json');a.full_accept(proofs['consumer-assurance-full-v2/result.json'],plan['image'])
    current=c.boundary();prior=a.protected(OUT/'web-boundary-v1/result.json')['boundary']
    a.only_assurance(current['workloads'],prior['workloads'])
    if {k:v for k,v in current.items() if k!='workloads'}!={k:v for k,v in prior.items() if k!='workloads'}:raise ValueError('Non-workload drift')
    live=a.b.request('GET',a.PATH+a.b.API)
    if a.settings_invariant(live)!=plan['invariant'] or live['properties']['template']['containers'][0]['image']!=plan['image']:raise ValueError('Assurance setting drift')
    c.links(False);health=c.health()
    run=subprocess.run([sys.executable,'-m','unittest','scripts.test_reporting_symbol_reconciliation_20261011','scripts.test_reporting_sector_deploy_20261011','scripts.test_reporting_assurance_deploy_20261011','scripts.test_reporting_web_receipt_20261011','-q'],cwd=ROOT,env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts'},capture_output=True,text=True,timeout=60)
    if run.returncode or re.findall(r'Ran (\d+) tests',run.stderr)!=['39']:raise ValueError('Regression guards failed')
    value={'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),
      'evidence':{n:p['protected_receipt'] for n,p in proofs.items()},'boundary':current,'website':health,
      'final_focused_tests_passed':39,'website_and_assurance_sector_correction_deployed':True,
      'broker_data_changed':False,'production_database_writes':0,'production_reports_or_approvals_changed':False,
      'private_qa_cache_rows_created':1,'private_qa_cache_transaction_rollback_verified':True,
      'symbol_resolution':'Exact dated conid-bound label reconciliation only; original records retained; no predecessor history inferred.',
      'remaining_gates':['Qualify and deploy older snapshot, historical, presentation and onboarding-render consumers against their own baselines',
        'Governed production snapshot and report correction, approval and download handover',
        'Historical visual acceptance beyond reviewed samples',
        'Private-network caller, secret refresh, cold-pull and external-denial acceptance',
        'Credential rotation with recovery and reconnection tests',
        'Wider website acceptance and genuine full scheduled cycle'],
      'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED',
      'whole_scheduled_cycle':'UNVERIFIED','human_alert_receipt':'UNVERIFIED',
      'independent_crisis_price_provider_certification':'UNVERIFIED','full_historical_visual_acceptance':'UNVERIFIED',
      'full_upgrade_complete':False}
    a.save(OUT,'closeout',value)
    print(json.dumps({'checkpoint_evidence_verified':True,'website':health,'full_upgrade_complete':False}))

if __name__=='__main__':main()
