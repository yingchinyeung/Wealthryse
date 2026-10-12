"""Image-only administration upgrade after bounded candidate/fallback acceptance."""
import copy, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
import control_upgrade_administration_runtime_20261010 as c
from deploy_upgrade_inception_20261009 import patch


def validate_trial(result, image, expected):
    if result.get('passed') is not True or result.get('image') != image or result.get('settings_preserved') is not True:
        raise ValueError('Successful exact-image preserved-settings trial required')
    c.accept(result['proof'], expected)


def main(target, stage):
    if target not in c.TARGETS: raise ValueError('Outside approved family')
    job, _, _ = c.TARGETS[target]; path = c.rid('Microsoft.App/jobs', job)
    image, source, proofs = c.candidate(target)
    root = c.OUT / (target + '-runtime-v1'); folder = root / 'deployment-v1'
    base = c.protected(root / 'baseline.json'); trials = {}
    for mode, wanted in (('candidate', image), ('fallback', source['base_image'])):
        result = c.protected(root / mode / 'result.json')
        validate_trial(result, wanted, proofs['baseline' if mode == 'fallback' else 'candidate'])
        trials[mode] = result['protected_receipt']
    live = c.request('GET', path + c.API); current = live['properties']['template']['containers'][0]['image']
    if c.settings_invariant(live) != base['invariant']: raise ValueError('Live non-image settings changed')
    controller_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if stage == 'plan':
        if folder.exists() or current != source['base_image']: raise ValueError('Existing plan or original image drift')
        body = patch(live, image); projected = copy.deepcopy(live); projected['properties']['template'] = body['properties']['template']
        if c.settings_invariant(projected) != base['invariant']: raise ValueError('Image-only boundary failed')
        c.save(folder, 'plan', {'image': image, 'before_image': source['base_image'], 'apply_body': body,
            'rollback_body': patch(live, source['base_image']), 'invariant': base['invariant'],
            'baseline_receipt': base['protected_receipt'], 'trial_receipts': trials, 'controller_sha256': controller_hash,
            'scope': 'Image only. Preserve entry point, schedule, credentials, authority/AI configuration, identities and all other settings.',
            'fallback_security_qualified': False, 'live_rollback_interruption_performed': False, 'full_upgrade_complete': False})
        print('Protected image-only deployment and exact fallback plan'); return
    plan = c.protected(folder / 'plan.json')
    if plan['controller_sha256'] != controller_hash or plan['image'] != image: raise ValueError('Controller/image binding changed')
    if stage == 'apply':
        c.runtime.safe_window(); c.runtime.idle(job)
        if current != source['base_image'] or (folder / 'apply-intent.json').exists(): raise ValueError('Reconcile existing mutation')
        c.save(folder, 'apply-intent', {'plan_receipt': plan['protected_receipt'], 'started_at': datetime.now(timezone.utc).isoformat()})
        # Recheck after archival to narrow overlap with the five-minute schedule.
        c.runtime.idle(job)
        newest = c.request('GET', path + c.API)
        if c.settings_invariant(newest) != base['invariant'] or newest['properties']['template']['containers'][0]['image'] != source['base_image']:
            raise ValueError('Live drift immediately before patch')
        ack = c.request('PATCH', path + c.API, data=plan['apply_body'])
        c.save(folder, 'apply-ack', {'state': ack.get('properties', {}).get('provisioningState')})
        print('Submitted image-only update; no normal administrative job started'); return
    if stage != 'verify': raise ValueError('Unknown stage')
    if current != image or live['properties']['provisioningState'] != 'Succeeded':
        print('Deployment not ready'); return
    if not (folder / 'verified.json').exists(): c.save(folder, 'verified', {'passed': True, 'image': image,
        'all_nonimage_settings_preserved': True, 'normal_job_started': False, 'permissions_changed': False,
        'reports_or_approvals_changed': False, 'full_upgrade_complete': False})
    print('Installed image verified; all other settings preserved')


if __name__ == '__main__': main(*sys.argv[1:])
