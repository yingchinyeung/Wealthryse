"""Seal the image-only onboarding correction and remaining scan findings."""
from datetime import datetime,timezone
import json,os,subprocess,sys
import control_upgrade_onboarding_runtime_20261010 as c
from plan_upgrade_owner_retirement_20261010 import load_plan,bindings,listing,BASE


def main():
    if (c.OUT/'closeout.json').exists():raise ValueError('Preserve sealed stage')
    image,source,proofs=c.candidate('onboarding');root=c.OUT/'onboarding-runtime-v1'
    deployment=c.protected(root/'deployment-v1/verified.json')
    if deployment.get('image')!=image or deployment.get('all_nonimage_settings_preserved') is not True:raise ValueError('Installation proof differs')
    trials={}
    for mode in ('candidate','fallback','installed'):
        r=c.protected(root/mode/'result.json');c.accept(r['proof'],proofs['baseline' if mode=='fallback' else 'candidate'])
        if r['image']!=(source['base_image'] if mode=='fallback' else image) or r.get('settings_preserved') is not True:raise ValueError('Trial identity differs')
        trials[mode]=r['protected_receipt']
    original=load_plan();expected=json.loads(json.dumps(original['non_target_bindings']))
    stage17=c.protected(c.ROOT/'.work/platform-upgrade-20261010-stage17/closeout.json')
    stage18=c.protected(c.ROOT/'.work/platform-upgrade-20261010-stage18/closeout.json')
    expected['ibkrv2stg-bootstrap']['images']=[stage17['bootstrap_image']]
    for r in stage18['deployments'].values():expected[r['job']]['images']=[r['image']]
    expected['ibkrv2stg-onboard-analytics']['images']=[image]
    actual=bindings(listing(BASE+'Microsoft.App/containerApps','2026-07-01')+listing(BASE+'Microsoft.App/jobs','2026-07-01'))
    if actual!=expected:raise ValueError('Unexpected workload changes')
    scans=c.protected(c.OUT/'remaining-scan-closeout.json');caller=c.protected(c.OUT/'caller-inspection.json')
    failure=c.protected(c.OUT/'onboarding-security-v1/failed-build.json')
    scan=json.loads((c.OUT/'onboarding-security-v2/scan-completed.json').read_bytes())
    tests={}
    for name,wanted in [('test_upgrade_stage19_scan_guards_20261010.py',6),('test_upgrade_onboarding_guards_20261010.py',6)]:
        p=subprocess.run([sys.executable,'scripts/'+name],cwd=c.ROOT,capture_output=True,text=True,timeout=60,
                         env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        log=p.stdout+p.stderr
        if p.returncode or 'Ran '+str(wanted)+' tests' not in log or '\nOK' not in log:raise ValueError('Controller guards failed')
        tests[name]={'tests':wanted,'log':log}
    remaining=[i for i in scans['images'] if i['declared_reference']!=source['base_image']]
    if len(remaining)!=12:raise ValueError('Unexpected remaining image count')
    c.save(c.OUT,'closeout',{'stage':'remaining-image-investigation-and-physical-onboarding-security','sealed_at':datetime.now(timezone.utc).isoformat(),
        'job':'ibkrv2stg-onboard-analytics','image':image,'source_files':proofs['candidate']['source_files'],
        'source_manifest_sha256':proofs['candidate']['source_manifest_sha256'],'tests_per_native_and_azure_trial':proofs['candidate']['tests'],
        'security_counts':scan['assessment']['counts'],'deployment_receipt':deployment['protected_receipt'],'trials':trials,
        'scan_investigation':scans['protected_receipt'],'caller_inspection':caller['protected_receipt'],
        'failed_test_scope_attempt':failure['protected_receipt'],'controller_guards':tests,
        'all_other_app_job_settings_and_images_preserved':True,'financial_calculations_source_and_reports_changed':False,
        'credentials_authentication_or_authority_changed':False,'normal_onboarding_executed':False,
        'fallback_security_qualified':False,'live_rollback_interruption_performed':False,
        'definitions':len(actual),'unique_images':len({i for b in actual.values() for i in b['images']}),
        'passing_scan_image_families':18,'remaining_blocked_or_unqualified_image_families':remaining,
        'remaining_gates':stage18['remaining_gates'],'real_viewer_acceptance':'Explicitly unverified by owner choice',
        'diagnostic_retirement_status':'Seven additional jobs authorized and archived; not yet retired at this checkpoint',
        'private_network_cutover_performed':False,'burn_in_complete':False,'full_upgrade_complete':False})
    print(json.dumps({'sealed':True,'job_upgraded':'ibkrv2stg-onboard-analytics','source_files_preserved':530,
                      'remaining_image_families':12,'full_upgrade_complete':False}))


if __name__=='__main__':main()
