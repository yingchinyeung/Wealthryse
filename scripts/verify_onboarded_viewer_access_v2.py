"""Read-only continuation: use the live website's database authorization mode."""
import argparse
import hashlib
import json
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import control_onboarded_viewer_access as original
from read_ms_website_input_probe import verify_env

p = original.p
STAGE = original.FOLDER / 'verify-v2'
WEB_PATH = p.release.BASE + 'Microsoft.App/containerApps/ibkrv2stg-web'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('stage', choices=('prepare', 'dispatch', 'inspect'))
    args = parser.parse_args()
    probe = p.release.read(original.FOLDER / 'probe', 'qualified')
    applied = p.release.read(original.FOLDER / 'apply', 'qualified')
    p.verify_protected(probe)
    p.verify_protected(applied)
    if applied['status'] != 'apply_passed' or applied['inserted_read_grants'] != 4:
        raise ValueError('Verified scoped four-grant transaction required')
    website = p.request('GET', WEB_PATH + p.release.API)
    web_container = website['properties']['template']['containers'][0]
    mode = [e for e in web_container['env'] if e['name'] == 'AUTHORIZATION_MODE']
    if (web_container['image'] != original.IMAGE or len(mode) != 1 or mode[0].get('value') != 'database'
            or mode[0].get('secretRef')):
        raise ValueError('Actual website image/database-only authorization differs')
    job = p.request('GET', p.release.path(p.JOB) + p.release.API)
    p.release.scope(job, p.JOB)
    if job['properties']['template']['containers'][0]['image'] != p.IMAGE:
        raise ValueError('Manual reporting image changed')
    config = {'phase': 'verify', 'web_sha256': original.WEB_HASH,
              'output_blob': original.PREFIX + 'verify-v2.json',
              'authorization': 'Owner requested viewers access all four onboarded accounts; read-only application verification',
              'probe_blob': original.PREFIX + 'probe.json', 'probe_sha256': probe['proof_sha256'],
              'website_authorization_mode': 'database', 'failed_original_verification_preserved': True}
    source_path = p.ROOT / 'scripts/run_onboarded_viewer_access.py'
    source = "import sys\nsys.path[:0]=['/app/src','/app/scripts']\nCONFIG=" + repr(config) + '\n' + source_path.read_text(encoding='utf-8')
    template = p.corrected_template(job, source)
    template['containers'][0]['image'] = original.IMAGE
    env = template['containers'][0]['env']
    if any(e['name'] == 'AUTHORIZATION_MODE' for e in env):
        raise ValueError('Preserve reporting-worker authorization; unexpected explicit override')
    env.append({'name': 'AUTHORIZATION_MODE', 'value': 'database'})
    if args.stage == 'prepare':
        if STAGE.exists():
            raise ValueError('Preserve original continuation')
        p.release.window()
        p.release.idle(p.JOB)
        STAGE.mkdir()
        record = {'template': template, 'config': config, 'source_sha256': p.sha(source_path),
                  'controller_sha256': p.sha(Path(__file__)), 'job_settings_sha256': p.release.settings_invariant(job),
                  'website_image': original.IMAGE, 'website_authorization_mode': 'database',
                  'failed_verification_execution': p.release.read(original.FOLDER / 'verify', 'dispatch')['execution'],
                  'correction': 'Copy only the nonsecret database authorization setting into this transient verification execution',
                  'persistent_settings_changed': False, 'database_writes': 0}
        p.release.build.save(STAGE, 'intent', {**record, 'protected_receipt': p.retain(record, 'four-account-viewer-verify-v2-intent')})
        print('PREPARED read-only verify-v2', flush=True)
        return
    intent = p.release.read(STAGE, 'intent')
    p.verify_protected(intent)
    if (intent['template'] != template or intent['config'] != config or intent['source_sha256'] != p.sha(source_path)
            or intent['controller_sha256'] != p.sha(Path(__file__)) or
            intent['job_settings_sha256'] != p.release.settings_invariant(job)):
        raise ValueError('Bound continuation or persistent settings differ')
    if args.stage == 'dispatch':
        if (STAGE / 'dispatch-intent.json').exists():
            raise ValueError('Inspect rather than repeat verification')
        p.release.window()
        p.release.idle(p.JOB)
        p.no_matching_execution(p.JOB, [template['containers'][0]['args']])
        p.release.build.save(STAGE, 'dispatch-intent', {'intent_sha256': p.sha(STAGE / 'intent.json')})
        ack = p.request('POST', p.release.path(p.JOB) + '/start' + p.release.API, data=template)
        if not ack.get('name'):
            raise ValueError('Unknown execution; inspect rather than repeat')
        p.release.build.save(STAGE, 'dispatch', {'execution': ack['name']})
        print(json.dumps({'execution': ack['name']}), flush=True)
        return
    name = p.release.read(STAGE, 'dispatch')['execution']
    run = p.request('GET', p.release.path(p.JOB) + '/executions/' + name + p.release.API)
    print(json.dumps({'execution': name, 'status': run['properties']['status']}), flush=True)
    if run['properties']['status'] != 'Succeeded':
        return
    actual = run['properties']['template']['containers']
    wanted = template['containers']
    if len(actual) != 1 or any(actual[0].get(k) != wanted[0].get(k) for k in ('name', 'image', 'command', 'args')):
        raise ValueError('Executed continuation source differs')
    verify_env(wanted[0]['env'], actual[0]['env'], job)
    blob = BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net', credential=AzureCliCredential()).get_blob_client(
        'internal-reports', config['output_blob'])
    content = blob.download_blob().readall()
    digest = hashlib.sha256(content).hexdigest()
    proof = json.loads(content)
    if (proof['configuration'] != config or proof['status'] != 'verify_passed' or proof['database_writes'] is not False
            or proof['missing'] or proof['viewer_count'] != 4 or len(proof['runtime_checks']) != 16
            or blob.get_blob_properties().metadata.get('sha256') != digest):
        raise ValueError('Read-only native viewer proof differs')
    if not (STAGE / 'proof.json').exists():
        with (STAGE / 'proof.json').open('xb') as stream:
            stream.write(content)
    elif (STAGE / 'proof.json').read_bytes() != content:
        raise ValueError('Prior proof differs')
    record = {'status': 'four_viewers_all_four_accounts_verified', 'execution': name, 'proof_sha256': digest,
              'viewers': 4, 'accounts': 4, 'viewer_account_checks': 16, 'missing_grants': 0, 'database_writes': 0,
              'application_owner_pages_denied': True, 'anonymous_denied': True, 'website_image': original.IMAGE,
              'website_authorization_mode': 'database', 'persistent_settings_preserved': True,
              'real_viewer_browser_sign_in_tested': False, 'failed_v1_preserved': True}
    if not (STAGE / 'qualified.json').exists():
        p.release.build.save(STAGE, 'qualified', {**record, 'protected_receipt': p.retain(record, 'four-account-viewer-verify-v2-passed')})
    print(json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
