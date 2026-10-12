"""Stage-scoped display-only web rollout: exact qualified image only.

This is not promotion of the complete infrastructure/worker upgrade. Its open
gates remain open; no worker, report, source, approval or network changes here.
"""
import copy
import json
import sys
from deploy_metadata_monitor import request, BASE
from deploy_ms_headers_v14r15 import clean, web_hash
from deploy_t_runtime_release_20261009 import safe_window
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected
from scan_ms_presentation_worker_candidate import verify
from release_platform_risk_labels_20261009 import BUNDLE, release

FOLDER = BUNDLE / 'web-deployment-v1'
WEB = BASE + 'Microsoft.App/containerApps/ibkrv2stg-web'
API = '?api-version=2024-03-01'
REVISION = 'risk-labels-1009-v1'


def read_protected(path):
    local = release.read(path)
    remote, _ = restore_protected(local['protected_receipt'])
    if remote != {k:v for k,v in local.items() if k != 'protected_receipt'}:
        raise ValueError('Protected evidence differs')
    return local


def save(name, value):
    release.save(FOLDER / (name + '.json'), {**value, 'protected_receipt': retain(value, 'platform-upgrade-web-' + name)})


def readiness_probe(value):
    containers = value['properties']['template']['containers']
    if len(containers) != 1:
        raise ValueError('Single reviewed application container required')
    probes = [p for p in containers[0]['probes'] if p['type'] == 'Readiness']
    if len(probes) != 1 or probes[0].get('httpGet', {}).get('path') not in ('/healthz', '/readyz'):
        raise ValueError('Unexpected readiness probe')
    return probes[0]


def invariant(value):
    normalized = clean(copy.deepcopy(value))
    if readiness_probe(normalized)['httpGet']['path'] != '/readyz':
        raise ValueError('Qualified readiness path must remain unchanged')
    return web_hash(normalized)


def record(path, *, expected_image):
    result = release.read(path)
    if result['status'] != 'Succeeded' or result['image'] != expected_image or not result['job_settings_preserved'] or len(result['records']) != 1:
        raise ValueError('Successful exact-image runtime acceptance required')
    return json.loads(result['records'][0]['Result'])


def gates():
    source, _, output = verify(release.WEB)
    image = output['registry'] + '/' + output['repository'] + '@' + output['digest']
    scan = release.read(release.WEB / 'scan-completed.json')
    remote, _ = restore_protected(release.read(release.WEB / 'scan-protected-receipt.json'))
    if scan != remote or scan['image'] != image or scan['assessment']['status'] != 'pass' or scan['assessment']['blocking_findings']:
        raise ValueError('Protected security gate failed')
    accepted = record(BUNDLE / 'candidate-display-probe-v1/result.json', expected_image=image)
    if (accepted.get('passed') is not True or accepted.get('tests_run')!=26
            or accepted.get('readiness_status')!=200 or accepted.get('read_only') is not True
            or accepted.get('database_writes')!=0 or accepted.get('model_calls')!=0
            or len(accepted['display_records']) != 5
            or set(accepted['source_sha256']) != {'web.py','risk_display.py'}
            or {r['subject'] for r in accepted['display_records']}!={'alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'}):
        raise ValueError('Private application canary failed')
    for name, digest in accepted['source_sha256'].items():
        if source['files_sha256']['src/ibkr_etl/' + name] != digest:
            raise ValueError('Runtime source differs from frozen release')
    rollback = record(BUNDLE / 'rollback-access-probe-v1/result.json', expected_image=release.IMAGE)
    if not rollback['successful'] or rollback['tests_run'] != 14 or rollback['errors'] or rollback['failures']:
        raise ValueError('Rollback authorization regression failed')
    return image


def main(stage):
    image = gates()
    current = request('GET', WEB + API)
    auth = request('GET', WEB + '/authConfigs/current' + API)
    actual = current['properties']['template']['containers'][0]['image']
    if stage == 'plan':
        if FOLDER.exists() or actual != release.IMAGE:
            raise ValueError('Existing plan or baseline drift')
        if current['properties']['configuration']['activeRevisionsMode'] != 'Single':
            raise ValueError('Unexpected revision policy')
        if any(s.get('value') for s in current['properties']['configuration'].get('secrets', [])):
            raise ValueError('Do not archive inline secrets')
        if readiness_probe(current)['httpGet']['path'] != '/readyz':
            raise ValueError('Readiness baseline drifted')
        template = clean(copy.deepcopy(current['properties']['template']))
        for container in template['containers']:
            for env in container.get('env', []):
                if env.get('secretRef') and env.get('value') == '':
                    env.pop('value')
        rollback = copy.deepcopy(template)
        template['containers'][0]['image'] = image
        template['revisionSuffix'] = REVISION
        simulated = copy.deepcopy(current)
        simulated['properties']['template'] = template
        readiness_probe(simulated)['httpGet']['path'] = '/readyz'
        if invariant(simulated) != invariant(current):
            raise ValueError('Unapproved settings change')
        restored = copy.deepcopy(simulated)
        restored['properties']['template'] = rollback
        if web_hash(clean(restored)) != web_hash(clean(current)) or rollback['containers'][0]['image'] != release.IMAGE:
            raise ValueError('Rollback roundtrip failed')
        prior = read_protected(release.ROOT / '.work/platform-upgrade-20261009-v1/web-deployment-v1/verified.json')
        if prior['image'] != release.IMAGE or prior['status'] != 'passed':
            raise ValueError('Qualified secure fallback missing')
        FOLDER.mkdir()
        save('plan', {'before':current, 'auth':auth, 'invariant':invariant(current), 'image':image,
             'apply_body':{'properties':{'template':template}},
             'rollback_body':{'properties':{'template':rollback}},
             'rollback_roundtrip_verified':True, 'rollback_runtime_verified':True,
             'authorization':'User requested deployment of upgrades after confirmation Grok stopped',
             'scope':'Risk display-only stage: image only; readiness/authentication/settings unchanged',
             'acceptance_evidence_sha256':{name:release.sha(BUNDLE/name) for name in (
                 'candidate-display-probe-v1/intent.json','candidate-display-probe-v1/result.json',
                 'rollback-access-probe-v1/intent.json','rollback-access-probe-v1/result.json',
                 'application-release-v1/source-intent.json','application-release-v1/scan-completed.json')},
             'controller_sha256':release.sha(__import__('pathlib').Path(__file__)),
             'not_complete_upgrade':True, 'remaining_gates':['new-month and governed regeneration qualification','MS extended-risk qualification','physical crisis refresh','real viewer acceptance','private endpoint and caller qualification','East Asia recovery exercises','real daily cycle/burn-in'],
             'database_writes':0,'jobs_changed':False,'report_bytes_changed':False})
        print('Application-stage plan and rollback retained; complete upgrade remains open')
        return
    plan = read_protected(FOLDER / 'plan.json')
    if (plan['controller_sha256'] != release.sha(__import__('pathlib').Path(__file__))
            or any(release.sha(BUNDLE/name) != digest for name,digest in plan['acceptance_evidence_sha256'].items())):
        raise ValueError('Frozen deployment controls or acceptance evidence changed')
    if invariant(current) != plan['invariant'] or auth['properties'] != plan['auth']['properties'] or image != plan['image']:
        raise ValueError('Configuration, authentication or candidate drift')
    if stage == 'apply':
        safe_window()
        if actual != release.IMAGE or readiness_probe(current)['httpGet']['path'] != '/readyz':
            raise ValueError('Baseline differs; reconcile, never blindly retry')
        save('apply-intent', {'from_image':actual,'to_image':image,'plan_sha256':release.sha(FOLDER/'plan.json'),
                             'readiness_from':'/readyz','readiness_to':'/readyz'})
        result = request('PATCH', WEB + API, data=plan['apply_body'])
        save('apply-ack', {'state':result.get('properties',{}).get('provisioningState'),'id':result.get('id')})
        print('Scoped website update submitted')
        return
    if stage != 'verify' or actual != image or readiness_probe(current)['httpGet']['path'] != '/readyz':
        raise ValueError('Installed candidate or readiness differs')
    props = current['properties']
    revision = 'ibkrv2stg-web--' + REVISION
    if props['provisioningState'] != 'Succeeded' or props['latestReadyRevisionName'] != revision:
        print(json.dumps({'waiting':True,'state':props['provisioningState']}))
        return
    running = request('GET', WEB + '/revisions/' + revision + API)['properties']
    if running['healthState'] != 'Healthy' or not running['active']:
        raise ValueError('New revision unhealthy; inspect rollback plan')
    traffic = props['configuration']['ingress']['traffic']
    if len(traffic) != 1 or traffic[0]['weight'] != 100 or traffic[0]['revisionName'] != revision:
        raise ValueError('Traffic mismatch')
    save('verified', {'status':'passed','image':image,'revision':revision,'authentication_preserved':True,
                     'other_settings_preserved':True,'readiness_path':'/readyz','jobs_changed':False,
                     'report_bytes_changed':False,'approvals_changed':False,'complete_upgrade':False})
    print('Application stage verified; remaining worker/data/infrastructure gates are not complete')


if __name__ == '__main__':
    main(sys.argv[1])
