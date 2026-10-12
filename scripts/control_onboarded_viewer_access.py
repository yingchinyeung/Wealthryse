"""Bound one-time viewer-read workflow; never changes job configuration or roles."""
import argparse
import hashlib
import json
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import run_ms_website_input_probe as p
from read_ms_website_input_probe import verify_env

FOLDER = p.ROOT / '.work/four-account-viewer-access-20261008-v1'
PREFIX = 'review/viewer-access/four-onboarded-accounts-20261008-v1/'
IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:5e4edb964065bd2e1d802610117b9ce9c2e13f666d87c969ae3e969731bebfef'
WEB_HASH = p.sha(p.ROOT / '.work/alpha-ms-website-benchmark-candidate-20261008-v2/context/src/ibkr_etl/web.py')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'dispatch', 'inspect'))
    parser.add_argument('--phase', choices=('probe', 'apply', 'verify'), default='probe')
    args = parser.parse_args()
    phase = args.phase
    stage = FOLDER / phase
    job = p.request('GET', p.release.path(p.JOB) + p.release.API)
    p.release.scope(job, p.JOB)
    if job['properties']['template']['containers'][0]['image'] != p.IMAGE:
        raise ValueError('Installed reporting job changed')
    config = {'phase': phase, 'web_sha256': WEB_HASH, 'output_blob': PREFIX + phase + '.json',
              'authorization': 'Owner requested all existing viewers have read access to all four onboarded accounts',
              'probe_blob': None, 'probe_sha256': None}
    if phase != 'probe':
        prior = p.release.read(FOLDER / 'probe', 'qualified')
        p.verify_protected(prior)
        config.update(probe_blob=PREFIX + 'probe.json', probe_sha256=prior['proof_sha256'])
    if phase == 'verify' and (FOLDER / 'apply/dispatch-intent.json').exists():
        applied = p.release.read(FOLDER / 'apply', 'qualified')
        p.verify_protected(applied)
        if applied['status'] != 'apply_passed':
            raise ValueError('Inspect the grant transaction before verification')
    source_path = p.ROOT / 'scripts/run_onboarded_viewer_access.py'
    source = "import sys\nsys.path[:0]=['/app/src','/app/scripts']\nCONFIG=" + repr(config) + '\n' + source_path.read_text(encoding='utf-8')
    template = p.corrected_template(job, source)
    template['containers'][0]['image'] = IMAGE
    if args.stage == 'prepare':
        if stage.exists():
            raise ValueError('Preserve prior phase evidence')
        p.release.window()
        p.release.idle(p.JOB)
        stage.mkdir(parents=True)
        record = {'phase': phase, 'template': template, 'job_settings_sha256': p.release.settings_invariant(job),
                  'source_sha256': p.sha(source_path), 'controller_sha256': p.sha(Path(__file__)), 'config': config,
                  'persistent_grants_requested': phase == 'apply', 'identities_roles_activation_unchanged': True}
        p.release.build.save(stage, 'intent', {**record, 'protected_receipt': p.retain(record, 'four-account-viewer-' + phase + '-intent-v1')})
        print('PREPARED ' + phase, flush=True)
        return
    intent = p.release.read(stage, 'intent')
    p.verify_protected(intent)
    if (intent['template'] != template or intent['controller_sha256'] != p.sha(Path(__file__)) or
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
    if proof['configuration'] != config or proof['status'] != phase + '_passed' or blob.get_blob_properties().metadata.get('sha256') != digest:
        raise ValueError('Private phase result differs')
    if not (stage / 'proof.json').exists():
        with (stage / 'proof.json').open('xb') as stream:
            stream.write(content)
    elif (stage / 'proof.json').read_bytes() != content:
        raise ValueError('Original proof changed')
    record = {'status': proof['status'], 'phase': phase, 'execution': name, 'proof_sha256': digest,
              'viewers': proof['viewer_count'], 'missing_grants': len(proof['missing']),
              'inserted_read_grants': proof['inserted_read_grants'], 'can_insert': proof['can_insert'],
              'runtime_checks': len(proof['runtime_checks']), 'settings_preserved': True,
              'real_viewer_browser_sign_in_tested': False}
    if not (stage / 'qualified.json').exists():
        p.release.build.save(stage, 'qualified', {**record, 'protected_receipt': p.retain(record, 'four-account-viewer-' + phase + '-passed-v1')})
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
