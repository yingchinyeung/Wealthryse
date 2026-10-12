"""Run frozen candidate acceptance as a transient job; saved workloads unchanged."""
import hashlib
import json
import sys
import copy
from pathlib import Path
from deploy_metadata_monitor import request, BASE
from deploy_t_runtime_release_20261009 import idle
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from audit_azure_runtime import az
from scan_ms_presentation_worker_candidate import verify
from release_platform_upgrade_20261009 import BUNDLE, release

SOURCE = Path(__file__).with_name('probe_platform_upgrade_20261009.py')
OUT = BUNDLE / 'candidate-runtime-probe-v1'
JOB = 'ibkrv2stg-onboard-render'
PATH = BASE + 'Microsoft.App/jobs/' + JOB
API = '?api-version=2024-03-01'
REFRESH = False
ROLLBACK = False


def save(name, value):
    release.save(OUT / (name + '.json'), value)


def main(stage):
    _, _, output = verify(release.WEB)
    image = output['registry'] + '/' + output['repository'] + '@' + output['digest']
    if ROLLBACK:
        image = release.IMAGE
    if stage == 'start':
        if OUT.exists():
            raise ValueError('Existing probe; reconcile rather than redispatch')
        idle(JOB)
        job = request('GET', PATH + API)
        source = SOURCE.read_text(encoding='utf-8')
        if REFRESH:
            from rehearse_ms_worker_image_rollback import restore_protected
            from run_specialist_gateway_read_verification import retain
            scan = release.read(release.WEB / 'scan-completed.json')
            protected, _ = restore_protected(release.read(release.WEB / 'scan-protected-receipt.json'))
            if scan != protected or scan['image'] != image or scan['assessment']['status'] != 'pass':
                raise ValueError('Exact protected scan gate failed')
            rehearsal = release.read(BUNDLE / 't-cache-refresh-plan-v1/result.json')
            if rehearsal['status'] != 'Succeeded' or rehearsal['image'] != image or len(rehearsal['records']) != 1:
                raise ValueError('Successful exact-image cache rehearsal required')
            plan = json.loads(rehearsal['records'][0]['Result'])
            if (not plan['refresh_qualified'] or not plan['all_metrics_identical']
                    or any(r['changed_columns'] != ['last_seen_run_id'] for r in plan['changed_crisis_record_columns'])):
                raise ValueError('Only a qualified provenance-only refresh is authorized here')
            receipt = retain(plan, 'platform-upgrade-t-provenance-refresh-plan')
            source = 'REFRESH_PLAN = ' + repr(plan) + '\n' + source
        template = corrected_template(job, source)
        container = template['containers'][0]
        container['image'] = image
        keep = {'DATABASE_URL', 'PORTFOLIO_TENANT_ID', 'AZURE_CLIENT_ID'}
        container['env'] = [e for e in container.get('env', []) if e['name'] in keep] + [{'name': 'AUTHORIZATION_MODE', 'value': 'database'}]
        db = [e for e in container['env'] if e['name'] == 'DATABASE_URL']
        if len(db) != 1 or not db[0].get('secretRef') or db[0].get('value'):
            raise ValueError('Secret-reference-only database configuration required')
        OUT.mkdir()
        save('intent', {'template': template, 'job_invariant': settings_invariant(job),
             'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
             'database_writes': 1 if REFRESH else 0, 'model_calls': 0, 'image': image,
             'refresh_plan_receipt': receipt if REFRESH else None})
        ack = request('POST', PATH + '/start' + API, data=template)
        save('dispatch', {'execution': ack['name']})
        print(json.dumps({'execution': ack['name'], 'saved_jobs_changed': False}))
        return
    intent = release.read(OUT / 'intent.json')
    if settings_invariant(request('GET', PATH + API)) != intent['job_invariant']:
        raise ValueError('Saved job changed')
    execution = release.read(OUT / 'dispatch.json')['execution']
    run = request('GET', PATH + '/executions/' + execution + API)
    print(json.dumps({'execution': execution, 'status': run['properties']['status']}))
    if run['properties']['status'] not in ('Succeeded', 'Failed'):
        return
    actual = run['properties']['template']['containers'][0]
    expected = intent['template']['containers'][0]
    actual_env = copy.deepcopy(actual.get('env', []))
    expected_env = expected.get('env', [])
    for item in actual_env:
        # Jobs execution GET materializes the saved secret under this job-local
        # alias. The immutable submitted body and saved-job invariant bind its
        # original reference; never normalize arbitrary or plaintext values.
        if item['name'] == 'DATABASE_URL' and item.get('secretRef') == 'cappjob-' + JOB and not item.get('value'):
            original = next(e for e in expected_env if e['name'] == 'DATABASE_URL')
            item['secretRef'] = original['secretRef']
    if any(actual.get(k) != expected.get(k) for k in ('image', 'command', 'args')) or actual['image'] != image or actual_env != expected_env:
        raise ValueError('Executed candidate differs from intent')
    query = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '" + execution + "-' | where Log_s startswith 'PLATFORM_CORRECTION_PROBE ' | project TimeGenerated,Result=parse_json(substring(Log_s,26))"
    result = az('monitor', 'log-analytics', 'query', '-w', '5264a124-10cd-45f6-93a5-6d65c37e9bfb', '--analytics-query', query)
    if result:
        save('result', {'execution': execution, 'status': run['properties']['status'],
                       'records': result, 'job_settings_preserved': True, 'image': image})
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    if sys.argv[2:] == ['t-cache-diagnostic']:
        SOURCE = SOURCE.with_name('probe_platform_upgrade_t_cache_20261009.py')
        OUT = BUNDLE / 't-cache-diagnostic-v1'
    if sys.argv[2:] == ['t-cache-refresh-plan']:
        SOURCE = SOURCE.with_name('probe_platform_upgrade_cache_refresh_20261009.py')
        OUT = BUNDLE / 't-cache-refresh-plan-v1'
    if sys.argv[2:] == ['t-cache-refresh-apply']:
        SOURCE = SOURCE.with_name('refresh_platform_upgrade_t_cache_20261009.py')
        OUT = BUNDLE / 't-cache-refresh-apply-v1'
        REFRESH = True
    if sys.argv[2:] == ['acceptance-after-refresh']:
        OUT = BUNDLE / 'candidate-runtime-probe-v2'
    if sys.argv[2:] == ['rollback-runtime']:
        SOURCE = SOURCE.with_name('probe_platform_access_correction_20261009.py')
        OUT = BUNDLE / 'rollback-runtime-probe-v1'
        ROLLBACK = True
    main(sys.argv[1])
