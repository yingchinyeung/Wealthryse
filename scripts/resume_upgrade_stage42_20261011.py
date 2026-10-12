"""Verify the stage-41 protected boundary before continuing the upgrade."""
import json
from pathlib import Path
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
import control_upgrade_main_dns_stage41_v2_20261011 as dns
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage42/resume-v1'
def main():
    if OUT.exists():raise ValueError('Preserve prior resumption evidence')
    previous=protected(ROOT/'.work/platform-upgrade-20261011-stage41/closeout.json')
    boundary=dns.current_boundary_function()()
    if boundary!=previous['boundary']:raise ValueError('Live drift requires reconciliation')
    dns.original.links(False)
    health=dns.original.health()
    save(OUT,'result',{'passed':True,'previous_checkpoint':previous['protected_receipt'],
        'boundary':boundary,'website':health,'live_changes':False,
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'passed':True,'workloads':len(boundary['workloads']),'website':health}))
if __name__=='__main__':main()
