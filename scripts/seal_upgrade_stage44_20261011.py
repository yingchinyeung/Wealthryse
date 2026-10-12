"""Seal deployed handover correction without claiming broader completion."""
import json,os,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
import deploy_report_handover_20261011 as d
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=Path(__file__).resolve().parents[1];OUT=d.STAGE
def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve checkpoint')
    deployed=protected(d.OUT/'verified.json')
    if not deployed['passed']:raise ValueError('Verified website deployment required')
    acknowledgement=protected(d.OUT/'apply-reconciled.json')
    if not acknowledgement['passed'] or acknowledgement['deployment_resubmitted']:raise ValueError('Read-only acknowledgement reconciliation required')
    old=protected(ROOT/'.work/platform-upgrade-20261011-stage43/closeout.json')
    current=d.release.dns.current_boundary_function()()
    expected=json.loads(json.dumps(old['boundary']));expected['workloads']['ibkrv2stg-web']=current['workloads']['ibkrv2stg-web']
    if current!=expected or current['workloads']['ibkrv2stg-web']['images']!=[deployed['image']]:raise ValueError('Unrelated boundary changed')
    d.main('verify')
    image,candidate,fallback,migration=d.candidate()
    rehearsal=protected(OUT/'migration-v2/schema-rehearse-v1/result.json')
    if not rehearsal['proof']['rollback_verified']:raise ValueError('Rollback evidence required')
    run=subprocess.run([sys.executable,'-m','unittest','scripts.test_handover_role_boundary_20261011','scripts.test_handover_deployment_20261011','-q'],cwd=ROOT,env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts'},capture_output=True,text=True,timeout=60)
    if run.returncode or 'Ran 14 tests' not in run.stderr:raise ValueError('Guard tests failed')
    d.release.dns.original.links(False)
    save(OUT,'closeout',{'checkpoint_evidence_verified':True,'sealed_at':datetime.now(timezone.utc).isoformat(),'previous_checkpoint':old['protected_receipt'],
        'boundary':current,'website':d.release.dns.original.health(),'candidate_image':image,'handover_candidate_deployed':True,'production_schema_changed':True,
        'schema_migration':'062_report_design_updates.sql','schema_receipt':migration['protected_receipt'],'schema_rollback_receipt':rehearsal['protected_receipt'],
        'deployment_receipt':deployed['protected_receipt'],'acknowledgement_receipt':acknowledgement['protected_receipt'],'candidate_catalogue_receipt':candidate['protected_receipt'],'fallback_catalogue_receipt':fallback['protected_receipt'],
        'native_image_tests':72,'deployment_role_guard_tests':14,'approved_downloads_preserved':True,'new_approvals_granted':0,'report_files_changed':0,'broker_data_changed':False,
        'database_role_permissions_changed':False,'public_access_changed':False,'credentials_rotated':False,'remaining_gates':old['remaining_gates'][1:],
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','whole_scheduled_cycle':'UNVERIFIED','human_alert_receipt':'UNVERIFIED',
        'independent_crisis_price_provider_certification':'UNVERIFIED','full_historical_native_visual_review':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'checkpoint_verified':True,'handover_deployed':True,'full_upgrade_complete':False}))
if __name__=='__main__':main()
