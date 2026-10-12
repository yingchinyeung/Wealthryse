"""Exact-image broker cutover, preserving all auth, secrets, probes and ingress."""
import copy
import json
import sys
from datetime import datetime, timezone
from release_upgrade_broker_security_20261010 import OUT, SOURCE, APP, API
from control_upgrade_broker_http_20261010 import candidate
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from deploy_platform_access_correction_20261009 import invariant
from deploy_ms_headers_v14r15 import clean
from run_specialist_gateway_read_verification import retain
from deploy_t_runtime_release_20261009 import safe_window

FOLDER = OUT / 'deployment-v1'
SUFFIX = 'broker-security-1010-v1'
CHECKS = {'actual_uvicorn_health_ready', '/docs_disabled', '/redoc_disabled', '/openapi.json_disabled',
          'credential_read_route_absent', 'missing_auth_denied', 'wrong_auth_denied',
          'invalid_namespace_rejected', 'extra_field_rejected'}


def accept(value, image):
    proof = value.get('proof', {})
    if not value.get('passed') or value.get('image') != image or not value.get('saved_settings_preserved') or not proof.get('passed'):
        raise ValueError('Exact HTTP runtime acceptance required')
    if set(proof.get('checks', [])) != CHECKS or len(proof.get('checks', [])) != len(CHECKS):
        raise ValueError('All HTTP and authorization checks required')
    if not all(proof.get(k) for k in ('actual_server_process', 'synthetic_credentials_only', 'server_nonloopback_connections_denied')) or any(proof.get(k) != 0 for k in ('secret_writes', 'broker_calls', 'database_connections')):
        raise ValueError('Isolated no-secret-write qualification required')


def patch(current, image, suffix):
    if current['properties']['configuration']['ingress']['external'] or current['properties']['configuration']['activeRevisionsMode'] != 'Single' or len(current['properties']['template']['containers']) != 1:
        raise ValueError('Exact internal single-container broker required')
    template = clean(copy.deepcopy(current['properties']['template']))
    for env in template['containers'][0].get('env', []):
        if env.get('secretRef') and env.get('value') == '': env.pop('value')
    template['containers'][0]['image'] = image; template['revisionSuffix'] = suffix
    trial = copy.deepcopy(current); trial['properties']['template'] = template
    if invariant(trial) != invariant(current): raise ValueError('Non-image configuration changed')
    return {'properties': {'template': template}}


def save(name, value):
    receipt = retain(value, 'upgrade-broker-deployment-' + name)
    with (FOLDER / (name + '.json')).open('x', encoding='utf-8') as handle:
        json.dump({**value, 'protected_receipt': receipt}, handle, indent=2)


def traffic_verified(props, running, revision):
    traffic = props['configuration']['ingress']['traffic']
    if len(traffic) != 1 or traffic[0].get('weight') != 100 or running.get('trafficWeight') != 100:
        return False
    target = traffic[0]
    named = target.get('revisionName') == revision and target.get('latestRevision') in (None, False)
    latest = (target.get('latestRevision') is True and target.get('revisionName') in (None, revision)
              and props.get('latestRevisionName') == props.get('latestReadyRevisionName') == revision)
    return named or latest


def main(stage):
    image = candidate()
    accept(protected(OUT / 'http-candidate-v1/result.json'), image)
    accept(protected(OUT / 'http-fallback-v1/result.json'), SOURCE)
    native = json.loads((OUT / 'native-build-qualified.json').read_bytes())['proofs']
    if len(native) != 2 or any(p['tests'] != 14 or p['source_files'] != 461 for p in native) or any(native[0][k] != native[1][k] for k in ('source_manifest_sha256', 'broker_source_sha256')):
        raise ValueError('Exact native authorization tests and application preservation required')
    live = request('GET', APP + API)
    auth = request('GET', APP + '/authConfigs/current' + API, missing=True)
    actual = live['properties']['template']['containers'][0]['image']
    if stage == 'plan':
        before = json.loads((OUT / 'build-intent.json').read_bytes())['before_application']
        if FOLDER.exists() or actual != SOURCE or invariant(live) != before['invariant']:
            raise ValueError('Baseline drift or existing deployment plan')
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets', [])):
            raise ValueError('Never archive inline credentials')
        body = patch(live, image, SUFFIX)
        rollback = patch(live, SOURCE, 'broker-security-rollback-1010-v1')
        FOLDER.mkdir()
        save('plan', {'before': live, 'auth': auth, 'image': image, 'before_image': SOURCE,
                      'invariant': invariant(live), 'apply_body': body, 'rollback_body': rollback,
                      'application_files_unchanged': 461, 'source_sha256': native[0]['broker_source_sha256'],
                      'scope': 'OS/Python security runtime only. Internal ingress, managed identity, set-only permission, secrets, broker auth, probes, scale and callers unchanged.',
                      'rollback_limitation': 'Functional fallback passed all 14 native and nine HTTP checks; retains nine critical and 172 high OS findings, not security-approved.',
                      'full_upgrade_complete': False})
        print('Protected broker image-only plan and functional fallback prepared'); return
    plan = protected(FOLDER / 'plan.json')
    if invariant(live) != plan['invariant'] or auth != plan['auth'] or image != plan['image']:
        raise ValueError('Broker configuration/authentication drift')
    if stage in ('apply', 'restore'):
        safe_window()
        if actual != (SOURCE if stage == 'apply' else image) or (FOLDER / (stage + '-intent.json')).exists():
            raise ValueError('Reconcile prior mutation; never repeat it')
        save(stage + '-intent', {'checked_at': datetime.now(timezone.utc).isoformat(), 'from_image': actual,
                                'plan_receipt': plan['protected_receipt']})
        ack = request('PATCH', APP + API, data=plan['apply_body'] if stage == 'apply' else plan['rollback_body'])
        save(stage + '-ack', {'state': ack.get('properties', {}).get('provisioningState')})
        print('Submitted one broker image-only update'); return
    if stage != 'verify' or actual != image: raise ValueError('Installed image mismatch')
    revision = 'ibkrv2stg-credential-broker--' + SUFFIX
    props = live['properties']
    if props['provisioningState'] != 'Succeeded' or props['latestReadyRevisionName'] != revision:
        print(json.dumps({'waiting': True, 'state': props['provisioningState']})); return
    running = request('GET', APP + '/revisions/' + revision + API)['properties']
    traffic = props['configuration']['ingress']['traffic']
    if running['healthState'] != 'Healthy' or not running['active'] or not traffic_verified(props, running, revision):
        raise ValueError('New internal revision is not healthy with full traffic')
    if not (FOLDER / 'verified.json').exists():
        save('verified', {'passed': True, 'image': image, 'revision': revision, 'settings_and_auth_preserved': True,
                          'application_files_unchanged': 461, 'secret_values_changed': False, 'public_ingress_enabled': False,
                          'live_internal_http_verified': False, 'traffic': traffic, 'revision_traffic_weight': running['trafficWeight'],
                          'postflight_reconciliation': 'Initial check required a named traffic revision. Azure retained latestRevision=true. Exact latest and latest-ready revision plus actual revision trafficWeight=100 independently verified; no configuration change or redeployment.',
                          'full_upgrade_complete': False})
    print('Healthy broker revision verified; auth, secrets, permissions and probes preserved')


if __name__ == '__main__': main(sys.argv[1])
