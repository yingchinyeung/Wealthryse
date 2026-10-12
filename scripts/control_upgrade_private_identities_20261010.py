"""Private secret access using existing job identities; no saved settings changes."""
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from control_upgrade_legacy_recovery_probe_20261009 import protected
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage12/private-identities-v1'
API = '?api-version=2024-03-01'
JOBS = {'publisher': 'ibkrv2stg-month-end-factsheet', 'web': 'ibkrv2stg-inbox-acceptance-r2',
        'diagnostics': 'ibkrv2stg-burn-in-monitor', 'metadata': 'ibkrv2stg-metadata-monitor',
        'discovery': 'ibkrv2stg-flex-discovery'}
PEERS = {'ibkrv2stg-auhecwc4itaoo.vault.azure.net': '10.50.10.8',
         'ibkrv2flexauhecwc4ita.vault.azure.net': '10.50.10.7'}


def save(folder, name, value):
    receipt = retain(value, 'upgrade-private-identity-' + folder.name + '-' + name)
    with (folder / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump({**value, 'protected_receipt': receipt}, f, indent=2)


def main(stage, group):
    if group not in JOBS:
        raise ValueError('Unapproved identity group')
    name = JOBS[group]; folder = OUT / group
    path = rid('Microsoft.App/jobs', name)
    job = request('GET', path + API)
    if stage == 'start':
        if folder.exists():
            raise ValueError('Reconcile existing execution')
        runtime.safe_window(); runtime.idle(name)
        identities = job['identity'].get('userAssignedIdentities', {})
        if len(identities) != 1 or not job['properties']['environmentId'].lower().endswith('/ibkrv2stg-env'):
            raise ValueError('Single existing main-network identity required')
        identity, details = next(iter(identities.items()))
        if not details.get('clientId'):
            raise ValueError('Existing client identity required')
        targets = []
        for secret in job['properties']['configuration'].get('secrets', []):
            url = secret.get('keyVaultUrl')
            if not url:
                continue
            parsed = urlsplit(url)
            if parsed.hostname not in PEERS or parsed.query or secret.get('identity', '').lower() != identity.lower():
                raise ValueError('Unexpected secret path or identity')
            targets.append({'kind': 'secret', 'url': url + '?api-version=7.4',
                            'private_ip': PEERS[parsed.hostname], 'alias': secret['name']})
        if not 1 <= len(targets) <= 5:
            raise ValueError('Bounded existing secret references required')
        proof = protected(ROOT / '.work/platform-upgrade-20261010-stage12/sync-security-v4/closeout.json')
        if not proof['passed']:
            raise ValueError('Qualified diagnostic runtime required')
        code = 'TARGETS=' + repr(targets) + '\n' + Path(__file__).with_name('probe_upgrade_private_services_20261009.py').read_text(encoding='utf-8')
        template = corrected_template(job, code)
        container = template['containers'][0]
        container['image'] = proof['image']
        container['env'] = [{'name': 'AZURE_CLIENT_ID', 'value': details['clientId']}]
        # No resolved secret, DB URI, broker token or storage credential is
        # injected. The source never retains/logs any secret response value.
        folder.mkdir(parents=True)
        save(folder, 'intent', {'template': template, 'invariant': settings_invariant(job),
                               'identity': identity, 'targets': targets, 'source_sha256': hashlib.sha256(code.encode()).hexdigest(),
                               'scope': 'Existing identity and existing secret references over process-local private DNS only'})
        ack = request('POST', path + '/start' + API, data=template)
        save(folder, 'dispatch', {'execution': ack['name']})
        print(json.dumps({'group': group, 'execution': ack['name'], 'targets': len(targets)}))
        return
    if stage != 'read':
        raise ValueError('Unknown stage')
    intent = protected(folder / 'intent.json')
    if settings_invariant(job) != intent['invariant']:
        raise ValueError('Saved configuration changed')
    execution = protected(folder / 'dispatch.json')['execution']
    run = request('GET', path + '/executions/' + execution + API)
    actual = run['properties']['template']['containers'][0]
    expected = intent['template']['containers'][0]
    if any(actual.get(k) != expected.get(k) for k in ('image', 'command', 'args', 'env')):
        raise ValueError('Execution differs from intent')
    state = run['properties']['status']
    if state not in ('Succeeded', 'Failed'):
        print(json.dumps({'group': group, 'state': state})); return
    prefix = 'PRIVATE_SERVICE_PROOF '
    query = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution + "-' | where Log_s startswith '" + prefix + "' | project Result=parse_json(substring(Log_s," + str(len(prefix)) + '))'
    rows = az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb', '--analytics-query', query)
    if len(rows) != 1:
        print(json.dumps({'group': group, 'state': state, 'proof_available': False})); return
    result = json.loads(rows[0]['Result'])
    expected_peers = {(t['alias'], t['private_ip']) for t in intent['targets']}
    actual_peers = {(t['alias'], t['private_peer']) for t in result['results'] if t['status'] == 200}
    passed = state == 'Succeeded' and result['passed'] and expected_peers == actual_peers and not result['secret_values_retained'] and not result['production_dns_changed'] and result['database_writes'] == result['blob_writes'] == 0
    value = {'passed': passed, 'group': group, 'execution': execution, 'proof': result,
             'saved_settings_preserved': True, 'private_cold_pull_verified': False, 'public_access_changed': False}
    if not (folder / 'result.json').exists():
        save(folder, 'result', value)
    print(json.dumps({'group': group, 'passed': passed, 'targets': len(result['results'])}))


if __name__ == '__main__':
    main(*sys.argv[1:])
