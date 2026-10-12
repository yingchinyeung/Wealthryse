"""Seal only the completed institutional-family stage, never the whole upgrade."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from release_upgrade_institutional_security_20261010 import OUT, JOBS, API
from control_upgrade_institutional_postgres_20261010 import candidate, accept, save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant


def main():
    if (OUT/'closeout.json').exists():raise ValueError('Preserve existing sealed evidence')
    image=candidate();native=json.loads((OUT/'native-build-qualified.json').read_bytes())
    proof=next(p for p in native['proofs'] if p['stage']=='candidate')
    if proof['source_files']!=451 or proof['tests']!=23 or proof['skipped']:raise ValueError('Native qualification differs')
    rehearsals={}
    for mode in ('candidate','fallback'):
        value=protected(OUT/('postgres-'+mode+'-v1')/'result.json');accept(value['proof'])
        rehearsals[mode]=value['protected_receipt']
    deployments={};checks={}
    for name in JOBS:
        folder=OUT/'deployment-v1'/name
        plan=protected(folder/'plan.json');verified=protected(folder/'verified.json')
        live=request('GET',rid('Microsoft.App/jobs',name)+API)
        if not verified['passed'] or live['properties']['template']['containers'][0]['image']!=image or settings_invariant(live)!=plan['invariant'] or live['properties']['provisioningState']!='Succeeded':raise ValueError('Installed job drift')
        installed=protected(OUT/('installed-'+name+'-v1')/'result.json')
        if not installed['passed'] or installed['image']!=image or installed['proof']!=proof:raise ValueError('Installed native proof differs')
        deployments[name]=verified['protected_receipt'];checks[name]=installed['protected_receipt']
    scheduled=protected(OUT/'discovery-scheduled-v1/result.json')
    if not scheduled['passed'] or not scheduled['scheduled_idle_path_verified'] or scheduled['image']!=image:raise ValueError('Observed discovery schedule required')
    tests=subprocess.run([sys.executable,str(Path(__file__).with_name('test_upgrade_institutional_release_20261010.py'))],capture_output=True,text=True,timeout=60)
    if tests.returncode or 'Ran 6 tests' not in tests.stderr or 'OK' not in tests.stderr:raise ValueError('Deployment guards failed')
    scan=json.loads((OUT/'scan-completed.json').read_bytes())
    legacy_path=OUT.parent/'legacy-installed-scan-v1/image-scan-legacy-v1/verification.json'
    legacy=json.loads(legacy_path.read_bytes());remote,_=restore_protected(legacy['protected_receipt'])
    if remote!=legacy['record']:raise ValueError('Legacy scan evidence differs')
    save(OUT,'closeout',{'stage':'institutional-family-security','sealed_at':datetime.now(timezone.utc).isoformat(),
         'image':image,'deployed_jobs':list(JOBS),'native_source_files_unchanged':451,'native_tests_each_image':23,
         'native_source_manifest_sha256':proof['source_manifest_sha256'],'deployment_guard_tests':6,
         'postgres_rehearsals':rehearsals,'postgres_checks_each_image':9,'persistent_clone_tables_unchanged':92,
         'deployments':deployments,'installed_native_checks':checks,'discovery_scheduled_receipt':scheduled['protected_receipt'],
         'security_counts':scan['assessment']['counts'],'fallback_security_qualified':False,
         'new_legacy_scan':legacy['protected_receipt'],'legacy_security_counts':legacy['record']['assessment']['counts'],
         'legacy_remediation_deployed':False,'normal_institutional_sector_scheduled_cycle_verified':False,
         'financial_formulas_changed':False,'source_data_changed_by_release':False,'report_approvals_changed':False,
         'permissions_changed':False,'private_network_cutover_performed':False,'burn_in_complete':False,'full_upgrade_complete':False})
    print(json.dumps({'sealed':True,'jobs':3,'image':image,'full_upgrade_complete':False}))


if __name__=='__main__':main()
