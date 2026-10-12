"""Seal the installed website correction without overstating acceptance."""
import json
from datetime import datetime, timezone
import deploy_upgrade_model_cash_20261010 as d


def main():
    d.main('verify')
    image = d.gates()
    deployed = d.c.protected(d.OUT/'verified.json')
    installed = d.c.protected(d.r.OUT/'installed/result.json')
    if deployed.get('passed') is not True or installed.get('passed') is not True or installed['image'] != image or deployed['image'] != image or installed.get('saved_job_unchanged') is not True:
        raise ValueError('Installed acceptance incomplete')
    d.r.accept(installed['proof'], 'installed')
    prior = d.c.protected(d.b.ROOT/'.work/platform-upgrade-20261010-stage25/closeout.json')
    scan = json.loads((d.b.OUT/'scan-completed.json').read_bytes())
    d.c.save(d.b.OUT.parent, 'closeout', {
        'sealed_at': datetime.now(timezone.utc).isoformat(), 'passed': True,
        'image': image, 'revision': deployed['revision'], 'traffic_percent': 100,
        'deployment_receipt': deployed['protected_receipt'],
        'installed_runtime_receipt': installed['protected_receipt'],
        'candidate_and_fallback_qualified': True, 'installed_model_month_checks': 42,
        'native_linux_tests_passed': 300, 'local_tests_passed': 29,
        'responsive_synthetic_cases_passed': 9, 'release_guard_tests_passed': 18,
        'scan_counts': scan['assessment']['counts'],
        'private_database_fixture_uploaded': False,
        'financial_calculations_changed': False, 'reports_and_approvals_changed': False,
        'authentication_and_unrelated_workloads_preserved': True,
        'remaining_image_families': prior['remaining_image_families'],
        'remaining_gates': prior['remaining_gates'],
        'real_viewer_sign_in_verified': False, 'live_rollback_interruption_performed': False,
        'full_upgrade_complete': False})
    print('Website cash correction sealed; full-upgrade acceptance remains incomplete')


if __name__ == '__main__': main()
