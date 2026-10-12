"""Protect candidate readiness, without claiming deployment or output acceptance."""
import hashlib,json
from datetime import datetime,timezone
import release_reporting_onboard_20261011 as r
import control_upgrade_g04_preflight_20261010 as b
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
from rehearse_ms_worker_image_rollback import restore_protected
import scan_ms_presentation_worker_candidate as scan

def main():
    if (r.OUT.parent/'candidate-checkpoint.json').exists():raise ValueError('Preserve existing checkpoint')
    source,native,image=scan.verify(r.OUT)
    scanned=r.r.read(r.OUT/'scan-completed.json')
    remote,_=restore_protected(r.r.read(r.OUT/'scan-protected-receipt.json'))
    if remote!=scanned or scanned['assessment']['status']!='pass' or scanned['assessment']['blocking_findings']!=0:raise ValueError('Exact protected passing scan required')
    prior=protected(r.ROOT/'.work/platform-upgrade-20261011-stage35/closeout.json')
    if b.bindings(b.current_resources())!=prior['boundary']['workloads']:raise ValueError('Live workload drift')
    native_proof=r.r.read(r.OUT/'native-build-qualified.json')
    if native_proof['linux_tests']!=104 or not native_proof['proof']['unrelated_files_preserved']:raise ValueError('Native qualification incomplete')
    save(r.OUT.parent,'candidate-checkpoint',{'passed':True,'at':datetime.now(timezone.utc).isoformat(),
      'image':scanned['image'],'native_tests':104,'scan_counts':scanned['assessment']['counts'],
      'scan_receipt':r.r.read(r.OUT/'scan-protected-receipt.json'),'source_only':True,
      'private_data_uploaded':False,'saved_job_changed':False,'deployed':False,
      'installed_candidate_runtime_acceptance':False,'candidate_output_acceptance':False,
      'previous_deployment_checkpoint':prior['protected_receipt'],
      'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED',
      'full_upgrade_complete':False})
    print(json.dumps({'candidate_verified':True,'native_tests':104,'scan_counts':scanned['assessment']['counts'],'deployed':False,'full_upgrade_complete':False}))

if __name__=='__main__':main()
