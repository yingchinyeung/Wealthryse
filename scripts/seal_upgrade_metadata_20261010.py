"""Seal this narrow runtime release; never mark the full upgrade complete."""
from datetime import datetime, timezone
import json
from control_upgrade_metadata_runtime_20261010 import OUT, SOURCE, PATH, API, candidate, save
from deploy_upgrade_metadata_security_20261010 import accept
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant


def main():
    if (OUT / 'closeout.json').exists(): raise ValueError('Preserve sealed closeout')
    image = candidate()
    plan = protected(OUT / 'deployment-v1/plan.json')
    deployed = protected(OUT / 'deployment-v1/verified.json')
    installed = protected(OUT / 'runtime-installed-v2/result.json'); accept(installed, image)
    fallback = protected(OUT / 'runtime-fallback-v2/result.json'); accept(fallback, SOURCE)
    live = request('GET', PATH + API)
    if live['properties']['template']['containers'][0]['image'] != image or settings_invariant(live) != plan['invariant'] or not deployed['passed']:
        raise ValueError('Installation drift')
    native = json.loads((OUT / 'native-build-qualified.json').read_bytes())
    record = {'sealed_at': datetime.now(timezone.utc).isoformat(), 'passed': True, 'image': image,
        'scope': 'Metadata monitor security runtime only; no other shared-image jobs promoted.',
        'critical': 0, 'high': 0, 'unknown': 0, 'medium': 40, 'low': 19,
        'native_tests_per_image': 44, 'local_deployment_guard_tests': 6,
        'preserved_source_files': native['proofs'][0]['source_files'],
        'unchanged_inline_payload_sha256': native['proofs'][0]['payload_sha256'],
        'excluded_cross_module_tests': native['proofs'][0]['excluded_cross_module_tests'],
        'candidate_and_functional_fallback_readonly_runtime_passed': True,
        'installed_runtime_receipt': installed['protected_receipt'], 'deployment_receipt': deployed['protected_receipt'],
        'all_other_job_settings_preserved': True, 'financial_data_and_report_changes': False,
        'old_image_security_limitations': plan['rollback_limitation'],
        'normal_scheduled_cycle_verified': False, 'burn_in_complete': False, 'full_upgrade_complete': False}
    save(OUT, 'closeout', record)
    print(json.dumps({k: record[k] for k in ('passed', 'image', 'native_tests_per_image', 'preserved_source_files', 'full_upgrade_complete')}))


if __name__ == '__main__': main()
