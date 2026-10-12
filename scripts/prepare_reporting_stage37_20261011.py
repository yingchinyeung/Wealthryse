"""Continue from installed onboarding boundary; revalidate existing reporter candidate."""
import json
from pathlib import Path
import release_reporting_history_20261011 as r
import scan_ms_presentation_worker_candidate as scan
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
from rehearse_ms_worker_image_rollback import restore_protected
import control_upgrade_main_dns_20261011 as c
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage37'

def main():
    if (OUT/'candidate-checkpoint.json').exists():raise ValueError('Preserve candidate checkpoint')
    prior=protected(ROOT/'.work/platform-upgrade-20261011-stage36/closeout.json')
    if not prior['checkpoint_evidence_verified'] or not prior['onboarding_renderer_image_deployed']:raise ValueError('Installed onboarding checkpoint required')
    current=c.boundary()
    if current!=prior['boundary']:raise ValueError('Azure boundary drift')
    source,_,build=scan.verify(r.OUT);image=build['registry']+'/'+build['repository']+'@'+build['digest']
    scanned=r.r.read(r.OUT/'scan-completed.json');remote,_=restore_protected(r.r.read(r.OUT/'scan-protected-receipt.json'))
    native=r.r.read(r.OUT/'native-build-qualified.json')
    if scanned!=remote or scanned['image']!=image or scanned['assessment']['status']!='pass' or not native['qualified'] or native['linux_tests']!=104 or native['native_log_sha256']!=r.r.sha(r.OUT/'native-build.log'):raise ValueError('Exact protected scan and native build required')
    save(OUT/'resume-v1','result',{'passed':True,'boundary':current,'previous_checkpoint':prior['protected_receipt'],'real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED'})
    save(OUT,'candidate-checkpoint',{'passed':True,'image':image,'native_tests':104,'scan_counts':scanned['assessment']['counts'],'base_image':source['base_image'],'source_only':True,'private_data_uploaded':False,'deployed':False,'real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'passed':True,'candidate':image,'deployed':False}))

if __name__=='__main__':main()
