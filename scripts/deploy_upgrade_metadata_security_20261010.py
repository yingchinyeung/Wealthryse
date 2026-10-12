"""One image-only monitor deployment after native, security and live-read trials."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from control_upgrade_metadata_runtime_20261010 import OUT, SOURCE, PATH, API, JOB, candidate, save, validate_completion
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_upgrade_inception_20261009 import patch
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request
FOLDER = OUT / 'deployment-v1'


def accept(proof, image):
    if not proof.get('passed') or proof.get('image') != image or not proof.get('saved_settings_preserved'):
        raise ValueError('Exact verified execution required')
    if proof.get('read_only_runtime') is not True or proof.get('financial_writes') != 0:
        raise ValueError('Read-only runtime required')
    validate_completion(proof.get('proof', {}), proof.get('rows', []))


def schedule_window(now):
    # Avoid either of the unchanged half-hour scheduler starts by five minutes.
    seconds = now.minute * 60 + now.second
    if min((420 - seconds) % 1800, (seconds - 420) % 1800) < 300:
        raise ValueError('Too close to metadata schedule; wait for a safe window')


def main(stage):
    image = candidate()
    a = protected(OUT / 'runtime-candidate-v2/result.json'); accept(a, image)
    b = protected(OUT / 'runtime-fallback-v2/result.json'); accept(b, SOURCE)
    native = json.loads((OUT / 'native-build-qualified.json').read_bytes())['proofs']
    if len(native) != 2 or any(native[0][k] != native[1][k] for k in ('tests', 'source_files', 'source_manifest_sha256', 'payload_sha256', 'excluded_cross_module_tests')) or native[0]['tests'] != 44:
        raise ValueError('Exact native equality and 44 tests required')
    source = json.loads((OUT / 'source-intent.json').read_bytes())
    live = request('GET', PATH + API)
    if settings_invariant(live) != source['job_invariant']:
        raise ValueError('Job settings drift')
    actual = live['properties']['template']['containers'][0]['image']
    digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if stage == 'plan':
        if FOLDER.exists() or actual != SOURCE: raise ValueError('Existing plan or changed baseline')
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets', [])):
            raise ValueError('Resolved credential capture prohibited')
        body = patch(live, image); trial = copy.deepcopy(live); trial['properties']['template'] = body['properties']['template']
        if settings_invariant(trial) != settings_invariant(live): raise ValueError('Non-image update prohibited')
        FOLDER.mkdir()
        save(FOLDER, 'plan', {'before_image': SOURCE, 'image': image, 'before': live,
             'invariant': settings_invariant(live), 'apply_body': body, 'rollback_body': patch(live, SOURCE),
             'controller_sha256': digest, 'candidate_receipt': a['protected_receipt'], 'fallback_receipt': b['protected_receipt'],
             'source_files_unchanged': native[0]['source_files'], 'payload_sha256': native[0]['payload_sha256'],
             'scope': 'Metadata monitor OS/Python security image only. Exact inline source, schedule, identity, secret references, configuration and application bytes unchanged.',
             'rollback_limitation': 'Exact original image passed read-only runtime but retains six critical, 151 high and four unknown findings; functional fallback only, not security-qualified.',
             'full_upgrade_complete': False})
        print('Protected image-only plan and functional rollback recorded'); return
    plan = protected(FOLDER / 'plan.json')
    if plan['controller_sha256'] != digest or plan['invariant'] != settings_invariant(live) or plan['image'] != image:
        raise ValueError('Frozen deployment boundary drift')
    if stage in ('apply', 'restore'):
        runtime.safe_window(); schedule_window(datetime.now(timezone.utc)); runtime.idle(JOB)
        expected = SOURCE if stage == 'apply' else image
        if actual != expected or (FOLDER / (stage + '-intent.json')).exists():
            raise ValueError('Reconcile prior mutation, never repeat')
        save(FOLDER, stage + '-intent', {'started_at': datetime.now(timezone.utc).isoformat(), 'plan': plan['protected_receipt']})
        ack = request('PATCH', PATH + API, data=plan['apply_body'] if stage == 'apply' else plan['rollback_body'])
        save(FOLDER, stage + '-ack', {'state': ack.get('properties', {}).get('provisioningState')})
        print('One metadata image-only update submitted'); return
    if stage != 'verify': raise ValueError('Unknown stage')
    if actual != image or live['properties']['provisioningState'] != 'Succeeded':
        print('Update not yet verified'); return
    if not (FOLDER / 'verified.json').exists():
        save(FOLDER, 'verified', {'passed': True, 'image': image, 'all_other_settings_preserved': True,
             'application_and_inline_payload_unchanged': True, 'full_upgrade_complete': False})
    print('Metadata monitor image verified; installed execution remains a separate acceptance check')


if __name__ == '__main__': main(sys.argv[1])
