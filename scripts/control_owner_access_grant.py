"""Bound one-time owner-role grant for one registered user; never changes job configuration.

`run` performs probe -> apply -> verify in order, waiting for each execution and
stopping at the first phase that does not pass.
"""
import argparse
import hashlib
import json
import time
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import run_ms_website_input_probe as p
from read_ms_website_input_probe import verify_env

FOLDER = p.ROOT / '.work/owner-access-chloe-20261016-v1'
PREFIX = 'review/owner-access/chloe-20261016-v1/'
WEB_PATH = p.release.BASE + 'Microsoft.App/containerApps/ibkrv2stg-web'
PHASES = ('probe', 'apply', 'verify')


def live_website_image():
    website = p.request('GET', WEB_PATH + p.release.API)
    container = website['properties']['template']['containers'][0]
    mode = [e for e in container['env'] if e['name'] == 'AUTHORIZATION_MODE']
    if len(mode) != 1 or mode[0].get('value') != 'database' or mode[0].get('secretRef'):
        raise ValueError('Website is not in database-only authorization')
    return container['image']


def frozen(phase, job):
    config = {'phase': phase, 'web_sha256': None, 'output_blob': PREFIX + phase + '.json',
              'authorization': 'Owner asked that chloe.yoo@wealthryse.ai have the same website access as the owner',
              'probe_blob': None, 'probe_sha256': None}
    if phase != 'probe':
        prior = p.release.read(FOLDER / 'probe', 'qualified')
        p.verify_protected(prior)
        if prior['status'] != 'probe_passed' or prior['proof_sha256'] != p.sha(FOLDER / 'probe/proof.json'):
            raise ValueError('Passed probe required before any grant')
        config.update(probe_blob=PREFIX + 'probe.json', probe_sha256=prior['proof_sha256'],
                      web_sha256=p.release.read(FOLDER / 'probe', 'proof')['web_sha256'])
    if phase == 'verify':
        applied = p.release.read(FOLDER / 'apply', 'qualified')
        p.verify_protected(applied)
        if applied['status'] != 'apply_passed' or applied['owner_role_granted'] is not True:
            raise ValueError('Inspect the grant transaction before verification')
    image = live_website_image()
    if phase != 'probe' and image != p.release.read(FOLDER / 'probe', 'intent')['execution_image']:
        raise ValueError('Website image changed since the probe; start a new attempt')
    source_path = p.ROOT / 'scripts/run_owner_access_grant.py'
    source = "import sys\nsys.path[:0]=['/app/src','/app/scripts']\nCONFIG=" + repr(config) + '\n' + source_path.read_text(encoding='utf-8')
    template = p.corrected_template(job, source)
    template['containers'][0]['image'] = image
    if phase == 'verify':
        # The reporting worker defaults to legacy authorization; copy only the website's nonsecret setting.
        env = template['containers'][0]['env']
        if any(e['name'] == 'AUTHORIZATION_MODE' for e in env):
            raise ValueError('Preserve reporting-worker authorization; unexpected explicit override')
        env.append({'name': 'AUTHORIZATION_MODE', 'value': 'database'})
    return config, template, image, source_path


def stage_once(stage_name, phase):
    stage = FOLDER / phase
    job = p.request('GET', p.release.path(p.JOB) + p.release.API)
    p.release.scope(job, p.JOB)
    config, template, image, source_path = frozen(phase, job)
    if stage_name == 'prepare':
        if stage.exists():
            raise ValueError('Preserve prior phase evidence')
        p.release.window()
        p.release.idle(p.JOB)
        stage.mkdir(parents=True)
        record = {'phase': phase, 'template': template, 'job_settings_sha256': p.release.settings_invariant(job),
                  'installed_job_image': job['properties']['template']['containers'][0]['image'],
                  'execution_image': image, 'website_authorization_mode': 'database',
                  'source_sha256': p.sha(source_path), 'controller_sha256': p.sha(Path(__file__)), 'config': config,
                  'persistent_owner_role_requested': phase == 'apply', 'entitlements_unchanged': True}
        p.release.build.save(stage, 'intent', {**record, 'protected_receipt': p.retain(record, 'owner-access-' + phase + '-intent-v1')})
        print('PREPARED ' + phase, flush=True)
        return None
    intent = p.release.read(stage, 'intent')
    p.verify_protected(intent)
    if (intent['template'] != template or intent['config'] != config or intent['controller_sha256'] != p.sha(Path(__file__)) or
            intent['source_sha256'] != p.sha(source_path) or intent['job_settings_sha256'] != p.release.settings_invariant(job)):
        raise ValueError('Frozen workflow or persisted job changed')
    if stage_name == 'dispatch':
        if (stage / 'dispatch-intent.json').exists():
            raise ValueError('Inspect rather than repeat one-time dispatch')
        p.release.window()
        p.release.idle(p.JOB)
        p.no_matching_execution(p.JOB, [template['containers'][0]['args']])
        p.release.build.save(stage, 'dispatch-intent', {'phase': phase, 'intent_sha256': p.sha(stage / 'intent.json')})
        ack = p.request('POST', p.release.path(p.JOB) + '/start' + p.release.API, data=template)
        if not ack.get('name'):
            raise ValueError('Unknown dispatch outcome; do not repeat')
        p.release.build.save(stage, 'dispatch', {'execution': ack['name']})
        print(json.dumps({'execution': ack['name'], 'phase': phase}), flush=True)
        return None
    name = p.release.read(stage, 'dispatch')['execution']
    run = p.request('GET', p.release.path(p.JOB) + '/executions/' + name + p.release.API)
    status = run['properties']['status']
    print(json.dumps({'execution': name, 'status': status}), flush=True)
    if status != 'Succeeded':
        return status
    actual = run['properties']['template']['containers']
    expected = template['containers']
    if len(actual) != 1 or any(actual[0].get(k) != expected[0].get(k) for k in ('name', 'image', 'command', 'args')):
        raise ValueError('Executed website image or source differs')
    verify_env(expected[0]['env'], actual[0]['env'], job)
    blob = BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net', credential=AzureCliCredential()).get_blob_client(
        'internal-reports', config['output_blob'])
    content = blob.download_blob().readall()
    digest = hashlib.sha256(content).hexdigest()
    proof = json.loads(content)
    if proof['configuration'] != config or blob.get_blob_properties().metadata.get('sha256') != digest:
        raise ValueError('Private phase result differs')
    if not (stage / 'proof.json').exists():
        with (stage / 'proof.json').open('xb') as stream:
            stream.write(content)
    elif (stage / 'proof.json').read_bytes() != content:
        raise ValueError('Original proof changed')
    record = {'status': proof['status'], 'phase': phase, 'execution': name, 'proof_sha256': digest,
              'owner_role_granted': proof['owner_role_granted'], 'privileges': proof['privileges'],
              'runtime_checks': len(proof['runtime_checks']), 'blockers': proof['blockers'],
              'diagnostics': proof['diagnostics'], 'web_symbols_present': all(proof['web_symbols'].values()),
              'settings_preserved': True, 'real_browser_sign_in_tested': False}
    if not (stage / 'qualified.json').exists():
        p.release.build.save(stage, 'qualified', {**record, 'protected_receipt': p.retain(record, 'owner-access-' + phase + '-result-v1')})
    print(json.dumps(record), flush=True)
    if proof['status'] != phase + '_passed':
        raise ValueError(phase + ' did not pass; stop and report')
    return status


def run_all():
    for phase in PHASES:
        stage = FOLDER / phase
        if (stage / 'qualified.json').exists():
            stage_once('inspect', phase)
            continue
        if not stage.exists():
            stage_once('prepare', phase)
        if not (stage / 'dispatch-intent.json').exists():
            stage_once('dispatch', phase)
        for _ in range(60):
            status = stage_once('inspect', phase)
            if status == 'Succeeded':
                break
            if status in ('Failed', 'Stopped', 'Degraded'):
                raise ValueError(phase + ' execution ended ' + status + '; stop and report')
            time.sleep(20)
        else:
            raise ValueError(phase + ' execution still running after 20 minutes; re-run to keep inspecting')
    print('OWNER ACCESS GRANT COMPLETE', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'dispatch', 'inspect', 'run'))
    parser.add_argument('--phase', choices=PHASES, default='probe')
    args = parser.parse_args()
    if args.stage == 'run':
        run_all()
    else:
        stage_once(args.stage, args.phase)


if __name__ == '__main__':
    main()
