"""Exact source-preserving bootstrap image qualification; no real bootstrap run."""
import base64, copy, gzip, hashlib, json, sys
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant
from control_upgrade_readers_20261010 import save, SOURCE, API
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected
from scan_ms_presentation_worker_candidate import verify
from verify_installed_ms_worker_release import corrected_template
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261010-stage17/bootstrap-v1'
RELEASE = ROOT / '.work/platform-upgrade-20261010-stage17/owner-runtime-v2'
JOB = 'ibkrv2stg-bootstrap'; PATH = rid('Microsoft.App/jobs', JOB)
BASELINE = ROOT / '.work/platform-upgrade-20261010-stage17/owner-jobs-v1/ibkrv2stg-bootstrap/baseline.json'
PREFIX = 'UPGRADE_BOOTSTRAP_RUNTIME '


def candidate():
    _, _, output = verify(RELEASE)
    record = json.loads((RELEASE / 'scan-completed.json').read_bytes())
    remote, _ = restore_protected(json.loads((RELEASE / 'scan-protected-receipt.json').read_bytes()))
    image = output['registry'] + '/' + output['repository'] + '@' + output['digest']
    if remote != record or record['image'] != image or record['assessment']['status'] != 'pass' or record['assessment']['blocking_findings']: raise ValueError('Protected candidate scan required')
    native = json.loads((RELEASE / 'native-build-qualified.json').read_bytes())
    for name in ('source-intent', 'native-build-qualified'):
        archived, _ = restore_protected(record['protected_artifact_receipts'][name])
        raw = (RELEASE / (name + '.json')).read_bytes()
        if archived['image'] != image or archived['sha256'] != hashlib.sha256(raw).hexdigest() or archived['content'] != json.loads(raw): raise ValueError('Independent protected build evidence differs')
    return image, {p['stage']: p for p in native['proofs']}


def accept(proof, expected):
    if proof.get('passed') is not True or proof.get('runtime') != expected['runtime'] or proof.get('source_files') != 530 or proof.get('source_manifest_sha256') != expected['source_manifest_sha256']: raise ValueError('Runtime/source proof differs')
    if any(proof.get(k) is not True for k in ('entrypoint_imported', 'missing_credentials_rejected_before_network', 'verified_tls_readonly_connection')): raise ValueError('Bootstrap runtime checks incomplete')
    if any(type(proof.get(k)) is not int or proof[k] != 0 for k in ('financial_rows_read', 'database_writes', 'migrations_run')) or proof.get('roles_or_passwords_changed') is not False: raise ValueError('Bootstrap mutation boundary failed')


def main(stage, mode):
    if mode not in ('candidate', 'fallback', 'installed'): raise ValueError('Unknown trial')
    image, proofs = candidate(); base = protected(BASELINE); job = request('GET', PATH + API)
    if settings_invariant(job) != base['invariant'] or job['properties']['template']['containers'][0]['image'] != (image if mode == 'installed' else SOURCE): raise ValueError('Bootstrap live boundary drift')
    expected = proofs['baseline' if mode == 'fallback' else 'candidate']; folder = OUT / mode
    if stage == 'start':
        runtime.safe_window(); runtime.idle(JOB)
        if folder.exists(): raise ValueError('Reconcile existing trial')
        checker = (ROOT / 'scripts/owner_task_provider_runtime.py').read_bytes()
        if hashlib.sha256(checker).hexdigest() != '161db0ff2d9145f3fe72a2d96b0c6e8a2a71eec3fb2a3b0cff70e06aa85ec63d': raise ValueError('Checker drift')
        code = "import types\nruntime_check=types.ModuleType('runtime_check')\nexec(compile(" + repr(checker) + ",'unchanged-runtime-check','exec'),runtime_check.__dict__)\n"
        code += 'EXPECTED_RUNTIME=' + repr(expected['runtime']) + '\nEXPECTED_MANIFEST=' + repr(expected['source_manifest_sha256']) + '\n'
        code += (ROOT / 'scripts/probe_upgrade_bootstrap_runtime_20261010.py').read_text(encoding='utf-8')
        template = corrected_template(job, code); c = template['containers'][0]; c['image'] = SOURCE if mode == 'fallback' else image
        c['env'] = [e for e in c.get('env', []) if e['name'] == 'DATABASE_ADMIN_URL']
        if len(c['env']) != 1 or not c['env'][0].get('secretRef') or c['env'][0].get('value'): raise ValueError('Exactly one existing admin URL reference required')
        save(folder, 'intent', {'image': c['image'], 'template': template, 'baseline_receipt': base['protected_receipt'],
            'expected': expected, 'scope': 'Import, missing-credential rejection, exact source/runtime and SELECT-only verified TLS. Never invoke bootstrap with a valid credential.'})
        ack = request('POST', PATH + '/start' + API, data=template)
        save(folder, 'dispatch', {'execution': ack['name']})
        print(json.dumps({'mode': mode, 'execution': ack['name']})); return
    if stage != 'read': raise ValueError('Unknown stage')
    intent = protected(folder / 'intent.json'); name = protected(folder / 'dispatch.json')['execution']
    run = request('GET', PATH + '/executions/' + name + API); c = run['properties']['template']['containers'][0]; wanted = intent['template']['containers'][0]
    if any(c.get(k) != wanted.get(k) for k in ('image', 'command', 'args')): raise ValueError('Executed payload differs')
    from control_upgrade_readers_20261010 import execution_env
    execution_env(JOB, c.get('env') or [], wanted['env'])
    state = run['properties']['status']; print(json.dumps({'mode': mode, 'status': state}))
    if state not in ('Succeeded', 'Failed'): return
    query = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + name + "-' | where Log_s startswith '" + PREFIX + "' | project Result=parse_json(substring(Log_s," + str(len(PREFIX)) + '))'
    rows = az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb', '--analytics-query', query)
    if state == 'Failed':
        save(folder, 'failure', {'passed': False, 'execution': name, 'proof_rows': len(rows)})
        raise ValueError('Read-only bootstrap trial failed; no deployment')
    if len(rows) != 1: print('Awaiting bounded proof ingestion'); return
    proof = json.loads(rows[0]['Result']); accept(proof, expected)
    if not (folder / 'result.json').exists(): save(folder, 'result', {'passed': True, 'image': intent['image'], 'proof': proof, 'execution': name, 'settings_preserved': True})
    print(json.dumps({'mode': mode, 'passed': True}))


if __name__ == '__main__': main(*sys.argv[1:])
