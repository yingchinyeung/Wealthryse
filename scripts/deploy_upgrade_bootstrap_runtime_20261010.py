"""One bootstrap image-only patch after no-write candidate/fallback checks."""
import copy, hashlib, json, sys
from datetime import datetime, timezone
from pathlib import Path
import control_upgrade_bootstrap_runtime_20261010 as c
from deploy_upgrade_inception_20261009 import patch


def validate_trial(result, image, expected):
    if result.get('passed') is not True or result.get('image') != image or result.get('settings_preserved') is not True: raise ValueError('Exact successful trial required')
    c.accept(result['proof'], expected)


def main(stage):
    image, native = c.candidate(); base = c.protected(c.BASELINE)
    retirement = c.protected(c.ROOT / '.work/platform-upgrade-20261010-stage17/retirement-v1/verified.json')
    if retirement['retired_count'] != 22 or retirement.get('all_other_app_job_settings_and_images_preserved') is not True: raise ValueError('Finish retirement preservation verification first')
    trials = {}
    for mode, target in (('candidate', image), ('fallback', c.SOURCE)):
        result = c.protected(c.OUT / mode / 'result.json'); validate_trial(result, target, native['baseline' if mode == 'fallback' else 'candidate'])
        trials[mode] = result['protected_receipt']
    live = c.request('GET', c.PATH + c.API); current = live['properties']['template']['containers'][0]['image']
    if c.settings_invariant(live) != base['invariant']: raise ValueError('Bootstrap settings drift')
    folder = c.OUT / 'deployment-v1'; controller_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    if stage == 'plan':
        if folder.exists() or current != c.SOURCE: raise ValueError('Existing plan or image drift')
        body = patch(live, image); projection = copy.deepcopy(live); projection['properties']['template'] = body['properties']['template']
        if c.settings_invariant(projection) != base['invariant']: raise ValueError('Image-only boundary failed')
        c.save(folder, 'plan', {'image': image, 'before_image': c.SOURCE, 'apply_body': body, 'rollback_body': patch(live, c.SOURCE),
            'invariant': base['invariant'], 'trial_receipts': trials, 'controller_sha256': controller_hash,
            'scope': 'Image only; no bootstrap invocation, migrations, role/password/secret changes or other workload update.',
            'fallback_security_qualified': False, 'live_rollback_interruption_performed': False, 'full_upgrade_complete': False})
        print('Image-only bootstrap plan and functional fallback archived'); return
    plan = c.protected(folder / 'plan.json')
    if plan['controller_sha256'] != controller_hash or plan['image'] != image: raise ValueError('Controller/image binding changed')
    if stage == 'apply':
        c.runtime.safe_window(); c.runtime.idle(c.JOB)
        if current != c.SOURCE or (folder / 'apply-intent.json').exists(): raise ValueError('Reconcile prior mutation')
        c.save(folder, 'apply-intent', {'started_at': datetime.now(timezone.utc).isoformat(), 'plan_receipt': plan['protected_receipt']})
        ack = c.request('PATCH', c.PATH + c.API, data=plan['apply_body'])
        c.save(folder, 'apply-ack', {'state': ack.get('properties', {}).get('provisioningState')})
        print('Submitted bootstrap image only; no normal bootstrap execution'); return
    if stage != 'verify': raise ValueError('Unknown stage')
    if current != image or live['properties']['provisioningState'] != 'Succeeded': print('Deployment not ready'); return
    if not (folder / 'verified.json').exists(): c.save(folder, 'verified', {'passed': True, 'image': image,
        'all_nonimage_settings_preserved': True, 'migrations_run': 0, 'roles_or_passwords_changed': False, 'full_upgrade_complete': False})
    print('Bootstrap image installed; settings preserved')


if __name__ == '__main__': main(sys.argv[1])
