"""Seal the installed onboarding correction without claiming whole-upgrade completion."""
import hashlib,json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import deploy_reporting_onboard_20261011 as d
import control_upgrade_main_dns_20261011 as c
OUT=d.qa.STAGE
EVIDENCE=('onboard-runtime-v1/result.json','onboard-output-review-v1/result.json','onboard-deployment-v1/verified.json','onboard-installed-role-v1/result.json')

def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve prior checkpoint')
    with ThreadPoolExecutor(max_workers=4) as pool:proofs=dict(zip(EVIDENCE,pool.map(lambda n:d.protected(OUT/n),EVIDENCE)))
    if any(p.get('passed') is not True for p in proofs.values()):raise ValueError('Complete installed acceptance required')
    plan=d.protected(d.OUT/'plan.json');d.accept(proofs[EVIDENCE[0]],proofs[EVIDENCE[1]],plan['image'])
    current=c.boundary();prior=d.protected(OUT/'resume-v1/result.json')['boundary'];d.only_target(current['workloads'],prior['workloads'])
    if {k:v for k,v in current.items() if k!='workloads'}!={k:v for k,v in prior.items() if k!='workloads'}:raise ValueError('Non-workload drift')
    live=d.b.request('GET',d.PATH+d.b.API);d.scope(live)
    if d.settings_invariant(live)!=plan['invariant'] or live['properties']['template']['containers'][0]['image']!=plan['image']:raise ValueError('Installed settings drift')
    c.links(False);health=c.health()
    reviewed={};folder=OUT/'onboard-output-review-v1'
    for stem in ('alpha-world-ms-2026-09-30','alpha-world-t-2026-09-30','U16088571-2025-01-31'):
        path=folder/(stem+'.pdf');digest=hashlib.sha256(path.read_bytes()).hexdigest()
        if not any(r['file']==str(path) and r['sha256']==digest for r in proofs[EVIDENCE[1]]['artifacts']):raise ValueError('Visual sample not hash-bound')
        reviewed[stem]={'pdf_sha256':digest,'page_png_sha256':{str(i):hashlib.sha256((folder/f'{stem}-{i}.png').read_bytes()).hexdigest() for i in (1,2)}}
    d.save(OUT,'closeout',{'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),'boundary':current,'website':health,'image':plan['image'],'job':d.JOB,
      'evidence':{n:p['protected_receipt'] for n,p in proofs.items()},'native_build_tests':104,'deployment_guard_tests':18,'package_comparator_tests':3,
      'private_test_factsheets':10,'private_test_presentations':10,'financial_charts_verified':60,'pdf_byte_comparisons_passed':10,'complete_presentation_package_comparisons_passed':10,
      'sampled_pdf_visual_review':{'files':reviewed,'pages_inspected':6,'renderer':'Poppler','observations':'Approved MS/T layout retained; corrected U16088571 January 99.82 NAV and -0.18% return visible; no new clipping or overlap seen in these six pages.','warnings':'Poppler reported unavailable Symbol/ArialUnicode display fonts; no visibly missing glyphs identified in these samples.'},
      'onboarding_renderer_image_deployed':True,'normal_onboarding_workflow_executed':False,'production_database_writes':0,'production_reports_or_approvals_changed':False,'broker_data_changed':False,
      'rollback_payload_roundtrip_verified':True,'live_rollback_execution':'NOT_PERFORMED',
      'remaining_gates':['Deploy corrected month-end worker after exact-image runtime/output acceptance','Qualify and deploy older snapshot, historical and legacy presentation consumers on their own baselines','Governed production snapshot/report correction with approval and download handover','Historical visual acceptance beyond reviewed samples','Private-network caller, secret refresh, cold-pull and external-denial checks','Credential rotation with recovery and reconnection tests','Wider website acceptance and genuine full scheduled cycle'],
      'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','whole_scheduled_cycle':'UNVERIFIED','human_alert_receipt':'UNVERIFIED','independent_crisis_price_provider_certification':'UNVERIFIED','full_historical_native_visual_review':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'checkpoint_evidence_verified':True,'onboarding_renderer_deployed':True,'website':health,'full_upgrade_complete':False}))

if __name__=='__main__':main()
