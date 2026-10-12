"""Read-only live resumption guard; archive evidence without changing workloads."""
import json
from pathlib import Path
import control_upgrade_main_dns_20261011 as c
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261011-stage38/resume-v1'

def main():
    if OUT.exists():
        raise ValueError('Preserve prior resumption evidence')
    prior = protected(ROOT / '.work/platform-upgrade-20261011-stage37/closeout.json')
    if not prior['checkpoint_evidence_verified'] or not prior['month_end_reporting_image_deployed']:
        raise ValueError('Verified installed checkpoint required')
    current = c.boundary()
    if current != prior['boundary']:
        raise ValueError('Azure boundary drift; review before deployment')
    c.links(False)
    health = c.health()
    save(OUT, 'result', {'passed': True, 'boundary': current, 'website': health,
        'previous_checkpoint': prior['protected_receipt'], 'live_changes': False,
        'fourteen_day_observation': 'UNVERIFIED', 'real_viewer_sign_in': 'UNVERIFIED',
        'full_upgrade_complete': False})
    print(json.dumps({'passed': True, 'website': health, 'workloads': len(current['workloads'])}))

if __name__ == '__main__':
    main()
