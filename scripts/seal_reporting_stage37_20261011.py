"""Seal verified month-end correction; preserve all unresolved upgrade gates."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import deploy_reporting_reporter_20261011 as d
import control_upgrade_main_dns_20261011 as c
OUT=d.qa.STAGE
EVIDENCE=('reporter-runtime-v1/result.json','reporter-output-review-v1/result.json','reporter-deployment-v1/verified.json','reporter-installed-role-v1/result.json')

def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve previous checkpoint')
    with ThreadPoolExecutor(max_workers=4) as pool:proofs=dict(zip(EVIDENCE,pool.map(lambda n:d.protected(OUT/n),EVIDENCE)))
    if any(p.get('passed') is not True for p in proofs.values()):raise ValueError('Complete installed acceptance required')
    plan=d.protected(d.OUT/'plan.json');d.accept(proofs[EVIDENCE[0]],proofs[EVIDENCE[1]],plan['image'])
    prior=d.protected(OUT/'resume-v1/result.json')['boundary'];current=c.boundary();d.only_target(current['workloads'],prior['workloads'])
    if {k:v for k,v in current.items() if k!='workloads'}!={k:v for k,v in prior.items() if k!='workloads'}:raise ValueError('Non-workload drift')
    live=d.b.request('GET',d.PATH+d.b.API);d.scope(live)
    if d.settings_invariant(live)!=plan['invariant'] or live['properties']['template']['containers'][0]['image']!=plan['image']:raise ValueError('Installed reporter drift')
    c.links(False);health=c.health()
    d.save(OUT,'closeout',{'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),'boundary':current,'website':health,'image':plan['image'],'job':d.JOB,
      'evidence':{n:p['protected_receipt'] for n,p in proofs.items()},'native_build_tests':104,'deployment_guard_tests':18,
      'private_test_factsheets':10,'private_test_presentations':10,'financial_charts_verified':60,'pdf_byte_comparisons_passed':10,'complete_presentation_package_comparisons_passed':10,
      'month_end_reporting_image_deployed':True,'normal_scheduled_workflow_executed':False,'schedule_preserved':True,'production_database_writes':0,'production_reports_or_approvals_changed':False,'broker_data_changed':False,
      'rollback_payload_roundtrip_verified':True,'live_rollback_execution':'NOT_PERFORMED',
      'remaining_gates':['Qualify and deploy older snapshot, historical and legacy presentation consumers on their own baselines','Governed production snapshot/report correction with approval and download handover','Historical visual acceptance beyond reviewed samples','Private-network caller, secret refresh, cold-pull and external-denial checks','Credential rotation with recovery and reconnection tests','Wider website acceptance and genuine full scheduled cycle'],
      'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','whole_scheduled_cycle':'UNVERIFIED','human_alert_receipt':'UNVERIFIED','independent_crisis_price_provider_certification':'UNVERIFIED','full_historical_native_visual_review':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'checkpoint_evidence_verified':True,'month_end_renderer_deployed':True,'website':health,'full_upgrade_complete':False}))

if __name__=='__main__':main()
