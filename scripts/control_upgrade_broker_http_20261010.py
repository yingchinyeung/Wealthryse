"""Candidate/fallback HTTP process checks on transient, credential-free executions."""
import hashlib
import json
import sys
from pathlib import Path
from release_upgrade_broker_security_20261010 import OUT, SOURCE, APP, API
from scan_ms_presentation_worker_candidate import verify
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_platform_access_correction_20261009 import invariant
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request
JOB = 'ibkrv2stg-onboard-render'
PATH = rid('Microsoft.App/jobs', JOB)


def candidate():
    _, _, output = verify(OUT)
    image = output['registry'] + '/' + output['repository'] + '@' + output['digest']
    scan = json.loads((OUT / 'scan-completed.json').read_bytes())
    remote, _ = restore_protected(json.loads((OUT / 'scan-protected-receipt.json').read_bytes()))
    if scan != remote or scan['image'] != image or scan['assessment']['status'] != 'pass' or scan['assessment']['blocking_findings']:
        raise ValueError('Protected exact candidate security acceptance required')
    return image


def save(folder, name, value):
    receipt = retain(value, 'upgrade-broker-http-' + folder.name + '-' + name)
    with (folder / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump({**value, 'protected_receipt': receipt}, f, indent=2)


def main(stage, mode):
    if mode not in ('candidate', 'fallback'): raise ValueError('Unknown mode')
    image = candidate()
    if mode == 'fallback': image = SOURCE
    folder = OUT / ('http-' + mode + '-v1')
    app = request('GET', APP + API)
    before = json.loads((OUT / 'build-intent.json').read_bytes())['before_application']
    if app['properties']['template']['containers'][0]['image'] != SOURCE or invariant(app) != before['invariant']:
        raise ValueError('Live broker changed during qualification')
    job = request('GET', PATH + API)
    if stage == 'start':
        if folder.exists(): raise ValueError('Reconcile existing execution')
        runtime.safe_window(); runtime.idle(JOB)
        code = Path(__file__).with_name('probe_upgrade_broker_http_20261010.py').read_text(encoding='utf-8')
        template = corrected_template(job, code)
        c = template['containers'][0]; c['image'] = image; c['env'] = []
        folder.mkdir()
        save(folder, 'intent', {'image': image, 'template': template, 'job_invariant': settings_invariant(job),
                               'source_sha256': hashlib.sha256(code.encode()).hexdigest(),
                               'scope': 'Actual HTTP server with synthetic credentials and denied non-loopback networking; no live broker/DB/vault calls'})
        ack = request('POST', PATH + '/start' + API, data=template)
        save(folder, 'dispatch', {'execution': ack['name']})
        print(json.dumps({'mode': mode, 'execution': ack['name']})); return
    if stage != 'read': raise ValueError('Unknown stage')
    intent = protected(folder / 'intent.json'); execution = protected(folder / 'dispatch.json')['execution']
    if settings_invariant(job) != intent['job_invariant']: raise ValueError('Saved job drift')
    run = request('GET', PATH + '/executions/' + execution + API)
    actual = run['properties']['template']['containers'][0]; expected = intent['template']['containers'][0]
    if any(actual.get(k) != expected.get(k) for k in ('image', 'command', 'args')) or actual.get('env', []):
        raise ValueError('Executed credential-free check differs')
    state = run['properties']['status']
    if state not in ('Succeeded', 'Failed'):
        print(json.dumps({'mode': mode, 'state': state})); return
    prefix = 'BROKER_HTTP_PROOF '
    query = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution + "-' | where Log_s startswith '" + prefix + "' | project Result=parse_json(substring(Log_s," + str(len(prefix)) + '))'
    rows = az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb', '--analytics-query', query)
    if len(rows) != 1:
        print(json.dumps({'mode': mode, 'state': state, 'proof_available': False})); return
    proof = json.loads(rows[0]['Result'])
    passed = state == 'Succeeded' and proof['passed'] and len(set(proof['checks'])) == 9 and proof['actual_server_process'] and proof['synthetic_credentials_only'] and proof['server_nonloopback_connections_denied'] and all(proof[k] == 0 for k in ('secret_writes', 'broker_calls', 'database_connections'))
    value = {'passed': passed, 'image': image, 'execution': execution, 'proof': proof, 'saved_settings_preserved': True}
    if not (folder / 'result.json').exists(): save(folder, 'result', value)
    print(json.dumps({'mode': mode, 'passed': passed, 'checks': len(proof['checks'])}))


if __name__ == '__main__': main(*sys.argv[1:])
