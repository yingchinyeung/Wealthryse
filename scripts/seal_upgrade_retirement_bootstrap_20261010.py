"""Close the exact approved retirement and bootstrap runtime stage honestly."""
import json, os, re, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
import control_upgrade_bootstrap_runtime_20261010 as c
from plan_upgrade_owner_retirement_20261010 import load_plan, bindings, listing, BASE, OUT as RETIRED
from retire_upgrade_owner_jobs_20261010 import APPROVED, target


def main():
    folder = c.OUT.parent
    if (folder / 'closeout.json').exists(): raise ValueError('Preserve closeout evidence')
    image, native = c.candidate(); base = c.protected(c.BASELINE)
    retirement = c.protected(RETIRED / 'verified.json'); plan = load_plan()
    if retirement['retired_count'] != 22 or set(retirement['retired_jobs']) != APPROVED: raise ValueError('Retirement differs')
    for j in plan['jobs']:
        if c.request('GET', target(j) + c.API, missing=True) is not None: raise ValueError('Retired diagnostic reappeared')
    actual = bindings(listing(BASE + 'Microsoft.App/containerApps', '2026-07-01') + listing(BASE + 'Microsoft.App/jobs', '2026-07-01'))
    expected = json.loads(json.dumps(plan['non_target_bindings'])); expected[c.JOB]['images'] = [image]
    if actual != expected: raise ValueError('Non-target workload changed')
    deployed = c.protected(c.OUT / 'deployment-v1/verified.json')
    if deployed['image'] != image or deployed['all_nonimage_settings_preserved'] is not True: raise ValueError('Bootstrap deployment differs')
    trials = {}
    for mode in ('candidate', 'fallback', 'installed'):
        r = c.protected(c.OUT / mode / 'result.json'); c.accept(r['proof'], native['baseline' if mode == 'fallback' else 'candidate'])
        if r['image'] != (c.SOURCE if mode == 'fallback' else image) or not r['passed'] or not r['settings_preserved']: raise ValueError('Exact read-only trial required')
        trials[mode] = r['protected_receipt']
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(c.ROOT / 'src'), str(c.ROOT / 'scripts'))), 'PYTHONDONTWRITEBYTECODE': '1'}
    commands = [('scripts/test_upgrade_owner_static_20261010.py', 8), ('scripts/test_upgrade_owner_retirement_20261010.py', 7), ('scripts/test_upgrade_bootstrap_runtime_20261010.py', 6)]
    logs = []
    for name, count in commands:
        result = subprocess.run([sys.executable, name], cwd=c.ROOT, env=env, capture_output=True, text=True, timeout=60)
        log = result.stdout + result.stderr; logs.append(log)
        if result.returncode or 'Ran ' + str(count) + ' tests' not in log or '\nOK' not in log: raise ValueError('Guard regressions failed')
    dependency = c.protected(RETIRED / 'dependency-qualified.json')
    coverage = c.protected(folder / 'image-coverage-v1.json')
    if coverage['definitions'] != 43 or any(i['image'] == c.SOURCE for i in coverage['images']): raise ValueError('Retired vulnerable image still referenced')
    scan = json.loads((c.RELEASE / 'scan-completed.json').read_bytes())
    c.save(folder, 'closeout', {'stage': 'approved-diagnostic-retirement-and-bootstrap-runtime', 'sealed_at': datetime.now(timezone.utc).isoformat(),
        'retirement_receipt': retirement['protected_receipt'], 'retired_jobs': sorted(APPROVED), 'archived_execution_records': 27,
        'bootstrap_image': image, 'bootstrap_deployment_receipt': deployed['protected_receipt'], 'bootstrap_trials': trials,
        'application_files_preserved': 530, 'native_tests_per_image': 13, 'dependency_tests': dependency['tests'],
        'retirement_decoder_bootstrap_guard_tests': 21, 'guard_test_output': '\n'.join(logs),
        'security_counts': scan['assessment']['counts'], 'security_covered_types': scan['assessment']['covered_types'],
        'remaining_definitions': coverage['definitions'], 'unique_images': coverage['unique_images'],
        'remaining_unmatched_scan_images': coverage['unmatched_protected_scan_images'], 'coverage_receipt': coverage['protected_receipt'],
        'all_other_workload_settings_and_images_preserved': True, 'migrations_run': 0, 'roles_or_passwords_changed': False,
        'source_data_changed': False, 'reports_or_approvals_changed': False, 'private_network_cutover_performed': False,
        'fallback_security_qualified': False, 'normal_bootstrap_executed': False, 'real_viewer_acceptance': 'explicitly unverified by owner choice',
        'remaining_gates': ['remaining image-family qualification and secret monitoring', 'private-network cutover and all-caller tests',
            'full reporting generation/recovery acceptance', 'comprehensive responsive UI including model-cash display', 'genuine scheduled cycle and 14-day new-release burn-in'],
        'burn_in_complete': False, 'full_upgrade_complete': False})
    print(json.dumps({'sealed': True, 'retired_jobs': 22, 'bootstrap_upgraded': True, 'full_upgrade_complete': False}))


if __name__ == '__main__': main()
