"""Scanned sync candidate; synthetic temporary-table test on private clone."""
import hashlib
import json
from pathlib import Path
import sys
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from scan_ms_presentation_worker_candidate import verify
from rehearse_ms_worker_image_rollback import restore_protected
from control_upgrade_legacy_recovery_probe_20261009 import protected
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage12/sync-security-v4'
BASELINE_MODE = sys.argv[2:] == ['baseline']
if sys.argv[2:] and not BASELINE_MODE: raise ValueError('Unknown SQL qualification scope')
FOLDER = OUT / ('postgres-baseline-v1' if BASELINE_MODE else 'postgres-v1')
SOURCE = Path(__file__).with_name('probe_upgrade_sync_postgres_20261010.py')
JOB = 'ibkrv2stg-account-sync-daily'
PATH = rid('Microsoft.App/jobs', JOB)
API = '?api-version=2024-03-01'


def save(name, data):
    with (FOLDER / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump({**data, 'protected_receipt': retain(data, 'upgrade-sync-postgres-' + name)}, f, indent=2)


def candidate():
    source, _, output = verify(OUT)
    image = output['registry'] + '/' + output['repository'] + '@' + output['digest']
    scan = json.loads((OUT / 'scan-completed.json').read_bytes())
    remote, _ = restore_protected(json.loads((OUT / 'scan-protected-receipt.json').read_bytes()))
    if scan != remote or scan['image'] != image or scan['assessment']['status'] != 'pass' or scan['assessment']['blocking_findings']:
        raise ValueError('Exact protected passing candidate scan required')
    return source['base_image'], image


def main(stage):
    baseline, image = candidate()
    if BASELINE_MODE: image = baseline
    job = request('GET', PATH + API)
    if job['properties']['template']['containers'][0]['image'] != baseline:
        raise ValueError('Installed sync baseline changed')
    if stage == 'start':
        if FOLDER.exists(): raise ValueError('Reconcile existing test instead of repeating')
        runtime.safe_window(); runtime.idle(JOB)
        code = SOURCE.read_text(encoding='utf-8'); compile(code, 'sync-clone-only', 'exec')
        template = corrected_template(job, code); c = template['containers'][0]; c['image'] = image
        c['env'] = [e for e in c.get('env', []) if e['name'] in ('DATABASE_URL', 'PORTFOLIO_TENANT_ID')]
        if {e['name'] for e in c['env']} != {'DATABASE_URL', 'PORTFOLIO_TENANT_ID'} or not next(e for e in c['env'] if e['name'] == 'DATABASE_URL').get('secretRef'):
            raise ValueError('Exact secret-reference-only probe environment required')
        FOLDER.mkdir(); save('intent', {'image': image, 'template': template, 'job_invariant': settings_invariant(job), 'source_sha256': hashlib.sha256(code.encode()).hexdigest(), 'scope': 'Private recovery clone only; synthetic session-local tables and sequences, source public tables read-only, no broker credentials or storage writes'})
        ack = request('POST', PATH + '/start' + API, data=template)
        save('dispatch', {'execution': ack['name']}); print(json.dumps({'execution': ack['name']})); return
    if stage != 'read': raise ValueError('Unknown step')
    intent = protected(FOLDER / 'intent.json'); execution = protected(FOLDER / 'dispatch.json')['execution']
    if settings_invariant(job) != intent['job_invariant']: raise ValueError('Installed job settings drift')
    run = request('GET', PATH + '/executions/' + execution + API)
    actual = run['properties']['template']['containers'][0]; expected = intent['template']['containers'][0]
    if any(actual.get(k) != expected.get(k) for k in ('image', 'command', 'args')): raise ValueError('Executed probe differs')
    state = run['properties']['status']; print(json.dumps({'execution': execution, 'status': state}))
    if state not in ('Succeeded', 'Failed'): return
    prefix = 'SYNC_POSTGRES_PROOF '
    query = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution + "-' | where Log_s startswith '" + prefix + "' | project Result=parse_json(substring(Log_s," + str(len(prefix)) + '))'
    rows = az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb', '--analytics-query', query)
    if len(rows) != 1: print('Final proof not yet available; inspect execution diagnostics if failed'); return
    proof = json.loads(rows[0]['Result'])
    passed = state == 'Succeeded' and proof['passed'] and proof['production_writes'] == proof['persistent_clone_writes'] == 0 and len(proof['checks']) == 6
    if not (FOLDER / 'result.json').exists(): save('result', {'passed': passed, 'image': image, 'execution': execution, 'proof': proof, 'job_settings_preserved': True})
    print(json.dumps({'passed': passed, 'proof': proof}))


if __name__ == '__main__': main(sys.argv[1])
