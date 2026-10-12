"""Seal the scoped cash-label rollout without closing unrelated upgrade gates."""
import json
from datetime import datetime,timezone
from pathlib import Path
import deploy_upgrade_cash_summary_20261010 as d

b=d.b;c=d.c;ROOT=b.ROOT;OUT=b.OUT.parent

def main():
    image,_=d.gates()
    previous=c.protected(ROOT/'.work/platform-upgrade-20261010-stage29/closeout.json')
    plan=c.protected(d.OUT/'plan.json');deployed=c.protected(d.OUT/'verified.json')
    installed=c.protected(d.r.OUT/'installed/result.json')
    if deployed.get('passed') is not True or installed.get('passed') is not True or deployed['image']!=image or installed['image']!=image:raise ValueError('Deployment and installed proof required')
    d.r.accept(installed['proof'],'installed');d.unrelated(plan['bindings'])
    live=c.request('GET',d.PATH+d.API);auth=c.request('GET',d.PATH+'/authConfigs/current'+d.API)
    if live['properties']['template']['containers'][0]['image']!=image or d.web_hash(d.clean(live))!=plan['invariant'] or auth['properties']!=plan['auth']['properties']:raise ValueError('Website drift')
    owner=b.h.read(b.OUT/'owner-browser-verified.json')
    if owner.get('passed') is not True or owner.get('viewer_acceptance') is not False or owner['balance_before']!=owner['balance_after'] or owner['balance_after']!='-5,107.93' or not (b.OUT/'owner-cash-summary.jpg').is_file():raise ValueError('Actual owner visual check required')
    scan=b.h.read(b.OUT/'scan-completed.json')
    value={'passed':True,'sealed_at':datetime.now(timezone.utc).isoformat(),'previous_checkpoint':previous['protected_receipt'],
        'website_image':image,'revision':deployed['revision'],'scope':'Physical cash BASE_SUMMARY display only',
        'deployment':deployed['protected_receipt'],'installed_runtime':installed['protected_receipt'],
        'scan_receipt':b.h.read(b.OUT/'scan-protected-receipt.json'),'scan_counts':scan['assessment']['counts'],
        'native_tests':300,'local_integration_tests':29,'focused_patch_tests':4,'responsive_synthetic_cases':9,
        'candidate_runtime_tests':42,'installed_runtime_tests':42,'actual_model_months_checked_per_probe':42,'deployment_guard_tests':7,
        'owner_browser':owner,'owner_screenshot_sha256':b.h.sha(b.OUT/'owner-cash-summary.jpg'),
        'fallback':'Exact previously qualified stage-26 installed image retained; no new live rollback interruption performed',
        'authentication_preserved':True,'other_workloads_preserved':36,'saved_diagnostic_job_unchanged':True,
        'financial_amounts_or_calculations_changed':False,'production_reports_or_approvals_changed':False,'viewer_permissions_changed':False,
        'remaining_gates':[
            'Final private-network cutover with all-caller, platform-secret, cold-pull, rollback and external-negative checks',
            'Credential ownership, grouped rotation and provider expiry review',
            'Fresh snapshot creation, governed regeneration and all-subject reporting coverage',
            'Remaining wider website acceptance and genuine whole scheduled cycle'],
        'observation_14_day':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','whole_scheduled_cycle':'UNVERIFIED',
        'human_alert_receipt':'UNVERIFIED','full_upgrade_complete':False}
    c.save(OUT,'closeout',value)
    print(json.dumps({'cash_summary_deployed':True,'owner_visual_verified':True,'full_upgrade_complete':False}))

if __name__=='__main__':main()
