"""Seal the legacy runtime stage only after three installed qualifications."""
from datetime import datetime,timezone
import json,subprocess,sys
from pathlib import Path
from control_upgrade_legacy_ingestion_20261010 import OUT,candidate,accept,save
from release_upgrade_legacy_security_20261010 import JOBS,API,SOURCE
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant


def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve sealed evidence')
    image=candidate();native=json.loads((OUT/'native-build-qualified.json').read_bytes())
    proof=next(p for p in native['proofs'] if p['stage']=='candidate')
    if proof['source_files']!=63 or proof['tests']!=36 or proof['skipped']:raise ValueError('Native qualification differs')
    rehearsals={}
    for mode in ('candidate','fallback'):
        value=protected(OUT/('ingestion-'+mode+'-v4')/'result.json');accept(value['proof'])
        if value['image']!=(image if mode=='candidate' else SOURCE):raise ValueError('Rehearsal digest differs')
        rehearsals[mode]=value['protected_receipt']
    deployments={};checks={}
    for name in JOBS:
        folder=OUT/'deployment-v1'/name
        plan=protected(folder/'plan.json');verified=protected(folder/'verified.json')
        live=request('GET',rid('Microsoft.App/jobs',name)+API)
        if not verified['passed'] or live['properties']['template']['containers'][0]['image']!=image or settings_invariant(live)!=plan['invariant'] or live['properties']['provisioningState']!='Succeeded':raise ValueError('Installed drift')
        if live['properties']['configuration'].get('identitySettings',[])!=plan['before']['properties']['configuration'].get('identitySettings',[]):raise ValueError('Runtime identity changed')
        installed=protected(OUT/('installed-'+name+'-v1')/'result.json')
        if not installed['passed'] or installed['image']!=image or installed['proof']!=proof:raise ValueError('Installed native proof differs')
        deployments[name]=verified['protected_receipt'];checks[name]=installed['protected_receipt']
    tests=subprocess.run([sys.executable,str(Path(__file__).with_name('test_upgrade_legacy_release_20261010.py'))],capture_output=True,text=True,timeout=60)
    if tests.returncode or 'Ran 6 tests' not in tests.stderr or 'OK' not in tests.stderr:raise ValueError('Deployment guards failed')
    scan=json.loads((OUT/'scan-completed.json').read_bytes())
    save(OUT,'closeout',{'stage':'legacy-ingestion-security','sealed_at':datetime.now(timezone.utc).isoformat(),
        'image':image,'deployed_jobs':list(JOBS),'native_source_files_unchanged':63,'native_tests_each_image':36,
        'native_source_manifest_sha256':proof['source_manifest_sha256'],'deployment_guard_tests':6,
        'ingestion_rehearsals':rehearsals,'ingestion_checks_each_image':4,'persistent_clone_tables_unchanged':25,
        'deployments':deployments,'installed_native_checks':checks,'security_counts':scan['assessment']['counts'],
        'fallback_security_qualified':False,'fallback_security_counts':{'CRITICAL':0,'HIGH':51,'UNKNOWN':3},
        'failed_diagnostic_rehearsals_preserved':True,'normal_scheduled_cycle_verified':False,
        'financial_formulas_changed':False,'source_data_changed_by_release':False,'report_approvals_changed':False,
        'permissions_changed':False,'private_network_cutover_performed':False,'burn_in_complete':False,'full_upgrade_complete':False})
    print(json.dumps({'sealed':True,'jobs':3,'image':image,'full_upgrade_complete':False}))


if __name__=='__main__':main()
