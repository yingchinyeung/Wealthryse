"""Close scoped source-preserving updates without claiming complete upgrade."""
import json, os, subprocess, sys
from datetime import datetime, timezone
import control_upgrade_administration_runtime_20261010 as c
from plan_upgrade_owner_retirement_20261010 import load_plan, bindings, listing, BASE


def main():
    if (c.OUT / 'closeout.json').exists(): raise ValueError('Preserve sealed evidence')
    plan = load_plan()
    expected = json.loads(json.dumps(plan['non_target_bindings']))
    prior = c.protected(c.ROOT / '.work/platform-upgrade-20261010-stage17/closeout.json')
    expected['ibkrv2stg-bootstrap']['images'] = [prior['bootstrap_image']]
    records = {}
    for target in c.TARGETS:
        image, source, proofs = c.candidate(target)
        job = c.TARGETS[target][0]; expected[job]['images'] = [image]
        root = c.OUT / (target + '-runtime-v1')
        deployment = c.protected(root / 'deployment-v1/verified.json')
        if deployment['image'] != image or deployment['all_nonimage_settings_preserved'] is not True:
            raise ValueError('Deployment verification differs')
        trials = {}
        for mode in ('candidate', 'fallback', 'installed'):
            result = c.protected(root / mode / 'result.json')
            c.accept(result['proof'], proofs['baseline' if mode == 'fallback' else 'candidate'])
            if result['image'] != (source['base_image'] if mode == 'fallback' else image) or result['settings_preserved'] is not True:
                raise ValueError('Trial binding differs')
            trials[mode] = result['protected_receipt']
        scan = json.loads((c.release_folder(target) / 'scan-completed.json').read_bytes())
        records[target] = {'job': job, 'image': image, 'source_files': proofs['candidate']['source_files'],
            'source_manifest_sha256': proofs['candidate']['source_manifest_sha256'],
            'tests_per_native_and_azure_trial': proofs['candidate']['tests'], 'security_counts': scan['assessment']['counts'],
            'deployment_receipt': deployment['protected_receipt'], 'trials': trials}
    actual = bindings(listing(BASE + 'Microsoft.App/containerApps', '2026-07-01') + listing(BASE + 'Microsoft.App/jobs', '2026-07-01'))
    if actual != expected: raise ValueError('Unexpected app/job change')
    coverage = c.protected(c.OUT / 'image-coverage-v1.json')
    if coverage['definitions'] != 43 or coverage['passing_scan_images'] != 17 or coverage['unmatched_protected_scan_images'] != 13 or coverage['blocked_scan_images'] != 0:
        raise ValueError('Current image-evidence inventory differs')
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    check = subprocess.run([sys.executable, 'scripts/test_upgrade_administration_guards_20261010.py'], cwd=c.ROOT,
        env=env, text=True, capture_output=True, timeout=60)
    log = check.stdout + check.stderr
    if check.returncode or 'Ran 6 tests' not in log or '\nOK' not in log: raise ValueError('Guard tests failed')
    failure = c.protected(c.OUT / 'review-security-v1/failed-build.json')
    normal = c.protected(c.OUT / 'normal-run-v1/result.json')
    if normal['image'] != records['admin']['image'] or normal['status'] != 'HEALTHY' or normal['repair_actions'] != 0:
        raise ValueError('Observed normal run differs')
    c.save(c.OUT, 'closeout', {'stage': 'scheduled-administration-security', 'sealed_at': datetime.now(timezone.utc).isoformat(),
        'deployments': records, 'guard_tests': 6, 'guard_log': log, 'original_review_failure': failure['protected_receipt'],
        'existing_normal_administrator_run': normal['protected_receipt'],
        'all_other_app_job_settings_and_images_preserved': True, 'financial_source_changed_by_upgrade': False,
        'report_renderers_or_approvals_changed_by_upgrade': False, 'authority_or_ai_configuration_changed': False,
        'credentials_changed': False, 'normal_administrative_jobs_manually_started': False,
        'fallback_security_qualified': False, 'live_rollback_interruption_performed': False,
        'current_image_coverage': {'definitions': coverage['definitions'], 'unique_images': coverage['unique_images'],
            'passing_scan_images': coverage['passing_scan_images'], 'unmatched_scan_images': coverage['unmatched_protected_scan_images'],
            'receipt': coverage['protected_receipt']},
        'remaining_gates': prior['remaining_gates'], 'real_viewer_acceptance': 'explicitly unverified by owner choice',
        'private_network_cutover_performed': False, 'burn_in_complete': False, 'full_upgrade_complete': False})
    print(json.dumps({'sealed': True, 'jobs_upgraded': 2, 'unmatched_scan_images': 13, 'full_upgrade_complete': False}))


if __name__ == '__main__': main()
