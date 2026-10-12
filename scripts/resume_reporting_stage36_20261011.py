"""Read-only continuation from the verified stage-35 live boundary."""
import json
from pathlib import Path
from datetime import datetime,timezone
import control_upgrade_main_dns_20261011 as c
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage36/resume-v1'
def main():
    if OUT.exists():raise ValueError('Preserve earlier resume')
    prior=protected(ROOT/'.work/platform-upgrade-20261011-stage35/closeout.json')
    current=c.boundary()
    if current!=prior['boundary']:raise ValueError('Live boundary drift; reconcile before deployment')
    c.links(False);health=c.health()
    save(OUT,'result',{'passed':True,'at':datetime.now(timezone.utc).isoformat(),'boundary':current,'website':health,
      'previous_checkpoint':prior['protected_receipt'],'real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'passed':True,'website':health,'workloads':len(current['workloads'])}))
if __name__=='__main__':main()
