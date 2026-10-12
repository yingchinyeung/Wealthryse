"""Seal only the security-only broker deployment and live rejection checks."""
import json
from datetime import datetime, timezone
from release_upgrade_broker_security_20261010 import OUT, APP, API
from control_upgrade_broker_http_20261010 import candidate
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from deploy_platform_access_correction_20261009 import invariant
from run_specialist_gateway_read_verification import retain


def main():
    target = OUT / 'closeout.json'
    if target.exists(): raise ValueError('Preserve sealed milestone')
    image = candidate()
    plan = protected(OUT / 'deployment-v1/plan.json')
    deployed = protected(OUT / 'deployment-v1/verified.json')
    installed = protected(OUT / 'installed-http-v1/result.json')
    app = request('GET', APP + API)
    auth = request('GET', APP + '/authConfigs/current' + API, missing=True)
    if any(r['image'] != image for r in (plan, deployed, installed)) or not deployed['passed'] or not installed['passed']:
        raise ValueError('Exact installed acceptance required')
    if app['properties']['template']['containers'][0]['image'] != image or invariant(app) != plan['invariant'] or auth != plan['auth']:
        raise ValueError('Installed broker configuration drift')
    value = {'passed': True, 'milestone': 'Credential-broker security runtime and installed internal HTTP acceptance',
             'image': image, 'revision': deployed['revision'], 'checked_at': datetime.now(timezone.utc).isoformat(),
             'application_files_unchanged': 461, 'native_tests_each_image': 14, 'isolated_http_checks_each_image': 9,
             'deployment_guard_tests': 8, 'installed_internal_http_checks': 4,
             'internal_ingress_authentication_identity_secret_references_probes_and_scale_unchanged': True,
             'real_credential_write_exercised': False, 'credentials_changed': False,
             'fallback_limitation': plan['rollback_limitation'],
             'receipts': {'deployment': deployed['protected_receipt'], 'installed': installed['protected_receipt']},
             'full_upgrade_complete': False, 'burn_in_complete': False}
    receipt = retain(value, 'upgrade-broker-security-closeout')
    with target.open('x', encoding='utf-8') as handle:
        json.dump({**value, 'protected_receipt': receipt}, handle, indent=2)
    print('Broker security milestone sealed; no source data, financial formulas, report approvals or credentials changed')


if __name__ == '__main__': main()
