"""Read-only resumption after the two approved manual job retirements."""
import json
from pathlib import Path
import seal_reporting_stage39_20261011 as prior
import control_upgrade_main_dns_20261011 as c

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage40/resume-v1'

def main():
    if OUT.exists():raise ValueError('Preserve previous resumption')
    old=prior.a.r.protected(prior.OUT/'closeout.json')
    current=prior.boundary_after_retirement()
    if not old['checkpoint_evidence_verified'] or current!=old['boundary']:
        raise ValueError('Live configuration drift; review before further changes')
    c.links(False);health=c.health()
    prior.a.r.save(OUT,'result',{'passed':True,'boundary':current,'website':health,
        'previous_checkpoint':old['protected_receipt'],'live_changes':False,
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'passed':True,'workloads':len(current['workloads']),'website':health}))

if __name__=='__main__':main()
