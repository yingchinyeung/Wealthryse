"""Read-only real monitor executions: exact candidate and functional fallback."""
import copy
import json
import sys
from release_upgrade_metadata_security_20261010 import OUT, SOURCE, PATH, API, JOB
from scan_ms_presentation_worker_candidate import verify
from control_upgrade_legacy_recovery_probe_20261009 import protected
from rehearse_ms_worker_image_rollback import restore_protected
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request


def candidate():
    _, _, output = verify(OUT)
    image = output['registry'] + '/' + output['repository'] + '@' + output['digest']
    scan = json.loads((OUT / 'scan-completed.json').read_bytes())
    remote, _ = restore_protected(json.loads((OUT / 'scan-protected-receipt.json').read_bytes()))
    if scan != remote or scan['image'] != image or scan['assessment']['status'] != 'pass' or scan['assessment']['blocking_findings']:
        raise ValueError('Exact protected passing security scan required')
    return image


def save(folder, name, value):
    with (folder / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump({**value, 'protected_receipt': retain(value, 'upgrade-metadata-' + folder.name + '-' + name)}, f, indent=2)


def validate_completion(proof, rows):
    if proof.get('kind') != 'complete' or proof.get('mode') != 'shadow' or proof.get('access_check') != 'passed':
        raise ValueError('Verified shadow-only completion required')
    if not proof.get('inventory_complete') or proof.get('denied_column_checks') != 7 or proof.get('financial_reconciliation') != 'not_certified':
        raise ValueError('Monitor access/scope verification incomplete')
    if proof.get('records') != len(rows) or any(r.get('captured_at') != proof.get('captured_at') for r in rows):
        raise ValueError('Complete non-truncated record set required')
    if sorted(r.get('index', -1) for r in rows) != list(range(len(rows))):
        raise ValueError('Duplicate or missing monitor records')
    if any(r.get('kind') not in ('risk', 'finding', 'benchmark') for r in rows):
        raise ValueError('Unexpected monitor output')


def main(stage, mode):
    if mode not in ('candidate', 'fallback', 'installed'): raise ValueError('Unknown mode')
    target = candidate()
    image = SOURCE if mode == 'fallback' else target
    folder = OUT / ('runtime-' + mode + '-v2')
    job = request('GET', PATH + API)
    source = json.loads((OUT / 'source-intent.json').read_bytes())
    if settings_invariant(job) != source['job_invariant'] or job['properties']['template']['containers'][0]['image'] != (target if mode == 'installed' else SOURCE):
        raise ValueError('Saved monitor changed outside release boundary')
    if stage == 'start':
        if folder.exists(): raise ValueError('Reconcile existing execution, never redispatch')
        runtime.safe_window(); runtime.idle(JOB)
        if mode == 'candidate':
            prior = OUT / 'runtime-candidate-v1'
            if (prior / 'intent.json').exists():
                old = protected(prior / 'intent.json')
                # A rejected override must not be retried until we establish
                # that no execution with this exact new image was created.
                url = PATH + '/executions' + API
                from urllib.parse import urlsplit
                for _ in range(20):
                    page = request('GET', url)
                    for run in page.get('value', []):
                        if any(c.get('image') == old['image'] for c in run['properties'].get('template', {}).get('containers', [])):
                            raise ValueError('Prior candidate execution exists; reconcile it instead')
                    link = page.get('nextLink')
                    if not link: break
                    parsed = urlsplit(link)
                    if parsed.scheme != 'https' or parsed.netloc != 'management.azure.com' or parsed.path.lower() != (PATH + '/executions').lower():
                        raise ValueError('Unexpected execution pagination')
                    url = parsed.path + '?' + parsed.query
                else: raise ValueError('Execution inventory exceeded bound')
                if not (prior / 'reconciled-rejection.json').exists():
                    save(prior, 'reconciled-rejection', {'http_status': 400, 'exact_image_execution_found': False,
                         'saved_job_unchanged': True, 'replacement_removes_null_optional_template_fields': True})
        template = {k: copy.deepcopy(v) for k, v in job['properties']['template'].items() if v is not None}
        c = template['containers'][0]; c['image'] = image
        for e in c.get('env', []):
            if e.get('secretRef') and e.get('value') == '': e.pop('value')
        folder.mkdir()
        save(folder, 'intent', {'image': image, 'template': template, 'job_invariant': settings_invariant(job),
             'scope': 'Exact installed metadata command and existing restricted monitor identity; read-only transaction, no financial writes or credential changes'})
        ack = request('POST', PATH + '/start' + API, data=template)
        save(folder, 'dispatch', {'execution': ack['name']})
        print(json.dumps({'mode': mode, 'execution': ack['name']})); return
    if stage != 'read': raise ValueError('Unknown stage')
    intent = protected(folder / 'intent.json'); execution = protected(folder / 'dispatch.json')['execution']
    run = request('GET', PATH + '/executions/' + execution + API)
    actual = run['properties']['template']['containers'][0]; expected = intent['template']['containers'][0]
    if any(actual.get(k) != expected.get(k) for k in ('image', 'command', 'args')):
        raise ValueError('Actual image/command differs')
    env = actual.get('env', [])
    if {e['name'] for e in env} != {'DATABASE_URL', 'PORTFOLIO_TENANT_ID'} or len(env) != 2:
        raise ValueError('Unexpected runtime environment')
    for e in env:
        old = next(v for v in expected['env'] if v['name'] == e['name'])
        if e['name'] == 'DATABASE_URL':
            if e.get('secretRef') not in (old.get('secretRef'), 'cappjob-' + JOB) or e.get('value'):
                raise ValueError('Runtime DB credential must remain the expected job secret reference')
        elif e.get('value') != old.get('value'): raise ValueError('Tenant changed')
    state = run['properties']['status']
    if state not in ('Succeeded', 'Failed'):
        print(json.dumps({'mode': mode, 'state': state})); return
    prefix = 'METADATA_MONITOR '
    query = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution + "-' | where Log_s startswith '" + prefix + "' | project Result=parse_json(substring(Log_s," + str(len(prefix)) + '))'
    result = az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb', '--analytics-query', query)
    records = [json.loads(r['Result']) for r in result]
    completions = [r for r in records if r.get('kind') == 'complete']
    if state != 'Succeeded': raise ValueError('Runtime failed; inspect non-sensitive error markers')
    if len(completions) != 1:
        print(json.dumps({'state': state, 'completion_available': False})); return
    proof = completions[0]; rows = [r for r in records if r.get('kind') != 'complete']
    validate_completion(proof, rows)
    value = {'passed': True, 'image': image, 'execution': execution, 'proof': proof, 'rows': rows,
             'saved_settings_preserved': True, 'read_only_runtime': True, 'financial_writes': 0,
             'monitor_findings_are_not_financial_certification': True}
    if not (folder / 'result.json').exists(): save(folder, 'result', value)
    print(json.dumps({'mode': mode, 'passed': True, 'records': len(rows), 'monitor_status': proof.get('status')}))


if __name__ == '__main__': main(*sys.argv[1:])
