"""Seal only completed assurance milestone, never the overall upgrade."""
import json
from datetime import datetime, timezone
from release_upgrade_assurance_period_20261009 import OUT
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant
from run_specialist_gateway_read_verification import retain


def main():
    if (OUT / 'closeout.json').exists(): raise ValueError('Preserve sealed milestone')
    plan = protected(OUT / 'deployment-v1/plan.json')
    deployment = protected(OUT / 'deployment-v1/verified.json')
    cycle = protected(OUT / 'installed-cycle-v2/result.json')
    post = protected(OUT / 'final-postflight-v1/result.json')
    scan = json.loads((OUT / 'scan-completed.json').read_bytes())
    remote, _ = restore_protected(json.loads((OUT / 'scan-protected-receipt.json').read_bytes()))
    if scan != remote or scan['assessment']['status'] != 'pass' or scan['assessment']['blocking_findings']:
        raise ValueError('Exact protected security acceptance required')
    live = request('GET', rid('Microsoft.App/jobs', 'ibkrv2stg-analyst-assurance') + '?api-version=2024-03-01')
    image = live['properties']['template']['containers'][0]['image']
    if any(r['image'] != image for r in (deployment, cycle, scan, plan)) or settings_invariant(live) != plan['invariant']:
        raise ValueError('Installed assurance image or settings drift')
    expected = {'alpha-world-ms', 'alpha-world-t', 'hong-kong-china', 'U16088571', 'U16088606'}
    subjects = cycle['proof']['subjects']
    if not deployment['passed'] or not cycle['passed'] or len(subjects) != 5 or {s['subject_id'] for s in subjects} != expected or any(s['status'] != 'pass' for s in subjects):
        raise ValueError('All-five normal acceptance required')
    if post['status'] != 'Succeeded' or not post['job_settings_preserved'] or len(post['records']) != 1:
        raise ValueError('Independent postflight did not complete')
    proof = json.loads(post['records'][0]['Result'])
    if not proof['passed'] or proof['database_writes'] != 0 or proof['protected_table_differences'] or len(proof['consumer_records']) != 10:
        raise ValueError('Exact source/report preservation required')
    value = {'passed': True, 'milestone': 'Installed assurance correction and normal five-subject acceptance', 'image': image, 'five_subjects_passed': sorted(expected), 'consumer_checks': 10, 'protected_table_differences': [], 'normal_run': cycle['execution'], 'postflight_run': post['execution'], 'receipts': {'deployment': deployment['protected_receipt'], 'cycle': cycle['protected_receipt'], 'postflight': post['protected_receipt']}, 'checked_at': datetime.now(timezone.utc).isoformat(), 'scheduled_run_verified': False, 'burn_in_complete': False, 'full_upgrade_complete': False, 'source_calculations_templates_approvals_and_ai_settings_unchanged': True}
    with (OUT / 'closeout.json').open('x', encoding='utf-8') as f:
        json.dump({**value, 'protected_receipt': retain(value, 'upgrade-assurance-closeout')}, f, indent=2)
    print('Protected assurance milestone sealed; full upgrade and scheduled burn-in remain open')


if __name__ == '__main__': main()
