"""Seal the scoped worker postflight without declaring full-upgrade completion."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from deploy_metadata_monitor import request,BASE
from build_ms_presentation_worker_candidate import invariant
from rehearse_ms_worker_image_rollback import restore_protected
import deploy_platform_monthly_worker_v2_20261009 as deploy

def main():
    plan=deploy.read('plan');verified=deploy.read('verified')
    evidence=deploy.release.BUNDLE/'installed-process-probe-v1/result.json'
    raw=json.loads(evidence.read_bytes())
    if raw['image']!=verified['image'] or raw['status']!='Succeeded' or raw['job_settings_preserved'] is not True or len(raw['records'])!=1:
        raise ValueError('Installed acceptance is incomplete')
    result=json.loads(raw['records'][0]['Result'])
    if (result.get('passed') is not True or result.get('actual_registry_and_worker_subprocesses') is not True
        or result.get('packages_reused')!=5 or result.get('report_and_approval_state_unchanged') is not True
        or result.get('new_reports_generated')!=0 or result.get('database_mode')!='read_only'
        or result.get('worker_sha256')!=plan['candidate_acceptance']['worker_sha256']):
        raise ValueError('Installed preservation check failed')
    previous=plan['candidate_acceptance']['actual_process_qualification']['report_and_approval_state']
    if result['report_and_approval_state']!=previous:raise ValueError('Report/approval state changed since qualification')
    live=request('GET',deploy.PATH+deploy.API)
    if invariant(live)!=plan['invariant'] or live['properties']['template']['containers'][0]['image']!=verified['image']:
        raise ValueError('Live worker drift')
    web=request('GET',BASE+'Microsoft.App/containerApps/ibkrv2stg-web'+deploy.API)
    if web['properties']['template']['containers'][0]['image']!=deploy.release.release.IMAGE:
        raise ValueError('Website changed during worker deployment')
    deploy.save('postflight',{'verified_at':datetime.now(timezone.utc).isoformat(),
        'job':deploy.JOB,'image':verified['image'],'installed_execution':raw['execution'],
        'result_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest(),
        'actual_process_acceptance':result,'settings_preserved':True,'website_image_preserved':True,
        'recovery_image':plan['rollback_acceptance']['image'],'original_defective_image_not_recommended_for_rollback':True,
        'live_rollback_interruption_exercised':False,'new_month_generation_and_governed_regeneration_qualified':False,
        'real_viewer_session_verified':False,'full_upgrade_complete':False})
    print(json.dumps({'monthly_worker_postflight':'passed','accounts_verified':5,
        'existing_reports_and_approvals_preserved':True,'full_upgrade_complete':False}))

if __name__=='__main__':main()
