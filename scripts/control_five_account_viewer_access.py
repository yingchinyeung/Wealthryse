"""Bound one-time five-account viewer-read workflow; never changes job configuration or roles."""
import argparse
import hashlib
import json
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import run_ms_website_input_probe as p
from read_ms_website_input_probe import verify_env

FOLDER = p.ROOT / '.work/five-account-viewer-access-20261012-v1'
PREFIX = 'review/viewer-access/five-onboarded-accounts-20261012-v1/'
WEB_PATH = p.release.BASE + 'Microsoft.App/containerApps/ibkrv2stg-web'
# The live website runs this commit tag; executions pin the digest it resolved to on 12 October 2026.
WEB_TAG = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform:web-88ef703a0fe4e9b000e54123bd771a8c952c6dcc'
IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:415aaed4a307adc988ab7490a1134f671a87dbb997d081f2a569762026eeee0b'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'dispatch', 'inspect'))
    parser.add_argument('--phase', choices=('probe', 'apply', 'verify'), default='probe')
    args = parser.parse_args()
    phase = args.phase
    stage = FOLDER / phase
    job = p.request('GET', p.release.path(p.JOB) + p.release.API)
    p.release.scope(job, p.JOB)
    website = p.request('GET', WEB_PATH + p.release.API)
    web_container = website['properties']['template']['containers'][0]
    mode = [e for e in web_container['env'] if e['name'] == 'AUTHORIZATION_MODE']
    if (web_container['image'] != WEB_TAG or len(mode) != 1 or mode[0].get('value') != 'database'
            or mode[0].get('secretRef')):
        raise ValueError('Actual website image/database-only authorization differs')
    config = {'phase': phase, 'web_sha256': None, 'output_blob': PREFIX + phase + '.json',
              'authorization': 'Owner chose to grant the four existing registered viewers read access to alpha-world-t',
              'probe_blob': None, 'probe_sha256': None}
    if phase != 'probe':
        prior = p.release.read(FOLDER / 'probe', 'qualified')
        p.verify_protected(prior)
        if (prior['status'] != 'probe_passed' or prior['viewers'] != 4 or prior['missing_grants'] != 4
                or prior['can_insert'] is not True or prior['proof_sha256'] != p.sha(FOLDER / 'probe/proof.json')):
            raise ValueError('Passed four-viewer probe required before any grant')
        config.update(probe_blob=PREFIX + 'probe.json', probe_sha256=prior['proof_sha256'],
                      web_sha256=p.release.read(FOLDER / 'probe', 'proof')['web_sha256'])
    if phase == 'verify':
        applied = p.release.read(FOLDER / 'apply', 'qualified')
        p.verify_protected(applied)
        if applied['status'] != 'apply_passed' or applied['inserted_read_grants'] != 4:
            raise ValueError('Inspect the grant transaction before verification')
        config['website_authorization_mode'] = 'database'
    source_path = p.ROOT / 'scripts/run_five_account_viewer_access.py'
    source = "import sys\nsys.path[:0]=['/app/src','/app/scripts']\nCONFIG=" + repr(config) + '\n' + source_path.read_text(encoding='utf-8')
    template = p.corrected_template(job, source)
    template['containers'][0]['image'] = IMAGE
    if phase == 'verify':
        # The reporting worker defaults to legacy authorization; copy only the website's nonsecret setting.
        env = template['containers'][0]['env']
        if any(e['name'] == 'AUTHORIZATION_MODE' for e in env):
            raise ValueError('Preserve reporting-worker authorization; unexpected explicit override')
        env.append({'name': 'AUTHORIZATION_MODE', 'value': 'database'})
    if args.stage == 'prepare':
        if stage.exists():
            raise ValueError('Preserve prior phase evidence')
        p.release.window()
        p.release.idle(p.JOB)
        stage.mkdir(parents=True)
        record = {'phase': phase, 'template': template, 'job_settings_sha256': p.release.settings_invariant(job),
                  'installed_job_image': job['properties']['template']['containers'][0]['image'],
                  'website_image': WEB_TAG, 'execution_image': IMAGE, 'website_authorization_mode': 'database',
                  'source_sha256': p.sha(source_path), 'controller_sha256': p.sha(Path(__file__)), 'config': config,
                  'persistent_grants_requested': phase == 'apply', 'identities_roles_activation_unchanged': True}
        p.release.build.save(stage, 'intent', {**record, 'protected_receipt': p.retain(record, 'five-account-viewer-' + phase + '-intent-v1')})
        print('PREPARED ' + phase, flush=True)
        return
    intent = p.release.read(stage, 'intent')
    p.verify_protected(intent)
    if (intent['template'] != template or intent['config'] != config or intent['controller_sha256'] != p.sha(Path(__file__)) or
            intent['source_sha256'] != p.sha(source_path) or intent['job_settings_sha256'] != p.release.settings_invariant(job)):
        raise ValueError('Frozen workflow or persisted job changed')
    if args.stage == 'dispatch':
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
        return
    name = p.release.read(stage, 'dispatch')['execution']
    run = p.request('GET', p.release.path(p.JOB) + '/executions/' + name + p.release.API)
    print(json.dumps({'execution': name, 'status': run['properties']['status']}), flush=True)
    if run['properties']['status'] != 'Succeeded':
        return
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
              'viewers': proof['viewer_count'], 'missing_grants': len(proof['missing']),
              'inserted_read_grants': proof['inserted_read_grants'], 'can_insert': proof['can_insert'],
              'runtime_checks': len(proof['runtime_checks']), 'blockers': proof['blockers'],
              'diagnostics': proof['diagnostics'], 'web_symbols_present': all(proof['web_symbols'].values()),
              'settings_preserved': True, 'real_viewer_browser_sign_in_tested': False}
    if not (stage / 'qualified.json').exists():
        p.release.build.save(stage, 'qualified', {**record, 'protected_receipt': p.retain(record, 'five-account-viewer-' + phase + '-result-v1')})
    print(json.dumps(record), flush=True)
    if proof['status'] != phase + '_passed':
        raise ValueError('Phase did not pass; stop and report')


if __name__ == '__main__':
    main()
