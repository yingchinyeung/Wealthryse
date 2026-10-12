"""One-shot installed-image private render, followed by independent readback.

No normal queue consumer, database writes, approvals, release or activation.
Registration is a separate acceptance gate. Existing qualification is immutable.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import deploy_ms_presentation_worker_release as release
from deploy_metadata_monitor import request
from qualify_ms_presentation_worker_runtime import require_workbook_proof, verify_execution_projection, runtime_prefix
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain
from verify_installed_ms_worker_release import corrected_template, no_matching_execution

JOB = release.build.JOBS[1]
PREFIX = 'review/alpha-world-ms/installed-private-batch-20261008-v1/'


def installed_prefix(folder):
    if folder.name.startswith('alpha-ms-format-worker-candidate-'):
        return runtime_prefix(folder) + 'installed-private-batch/'
    return 'review/alpha-world-ms/installed-private-batch-20261008-v1/'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def protected(record):
    actual, _ = restore_protected(record['protected_receipt'])
    require(actual == {k: v for k, v in record.items() if k != 'protected_receipt'},
            'Protected record differs')


def package_parts(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), 'Duplicate package members prohibited')
        return {n: hashlib.sha256(archive.read(n)).hexdigest() for n in sorted(names)}


def prepare_source(folder, plan):
    binding = release.read(folder / 'runtime-v1', 'source-intent')
    probe = folder / 'runtime-v1/frozen-probe.py'
    require(release.build.sha(probe) == binding['probe_sha256'], 'Frozen renderer probe differs')
    preflight = release.build.ROOT / 'scripts/probe_alpha_ms_registered_batch.py'
    require(len(binding['periods']) == 21, 'Exactly 21 months required')
    inject = {'mode': 'candidate', 'image': plan['image'], 'prefix': PREFIX + 'render/',
              'content_contract_version': plan['binding']['content_contract_version'],
              'periods': binding['periods'],
              'installed_files_sha256': plan['binding']['installed_files_sha256']}
    # Execute existing probes in separate namespaces, with their entrypoints
    # disabled. Both enforce PostgreSQL READ ONLY; neither consumes the queue.
    source = "import os\nos.environ['MS_BATCH_PREFLIGHT_BLOB']=" + repr(PREFIX + 'preflight.json') + '\n'
    source += "ns={'__name__':'private_ms_preflight'}\nexec(compile(" + repr(preflight.read_text()) + ", 'private-ms-preflight', 'exec'), ns)\nns['main']()\n"
    source += "ns={'__name__':'private_ms_installed_render','PROBE_BINDING':" + repr(inject) + "}\nexec(compile(" + repr(probe.read_text()) + ", 'private-ms-installed-render', 'exec'), ns)\nns['main']()\n"
    return source, {'probe': binding['probe_sha256'], 'preflight': release.build.sha(preflight)}


def validate_proof(proof, intent, prior):
    require(proof['status'] == 'passed' and proof['image'] == intent['image']
            and proof['mode'] == 'candidate' and proof['count'] == 21
            and len(proof['artifacts']) == 21
            and proof['database_writes'] == 0 and proof['read_only_transaction_enforced'] is True
            and proof['owner_identity_verified'] is True and proof['source_data_changed'] is False
            and proof['reports_released'] is False and proof['onboarding_activated'] is False,
            'Installed render scope or coverage differs')
    old = {r['period_end']: r for r in prior['proof']['artifacts']}
    require(len(old) == 21 and {r['period_end'] for r in proof['artifacts']} == set(old),
            'Distinct historical month coverage differs')
    for row in proof['artifacts']:
        previous = old[row['period_end']]
        require(all(row[k] == previous[k] for k in ('snapshot_id', 'data_sha256', 'factsheet_sha256'))
                and row['financial_charts_verified'] == 6 and row['risk_metrics_verified'] == 7,
                'Accepted inputs, PDF content or financial coverage differs')
        require_workbook_proof(row)
        for kind, ext in (('factsheet', '.pdf'), ('presentation', '.pptx')):
            name = row[kind + '_blob']
            expected = ('AlphaWorld_MS_Factsheet_' if kind == 'factsheet' else 'AlphaWorld_MS_Presentation_') + row['period_end'] + ext
            require(name == PREFIX + 'render/outputs/' + expected, 'Unexpected private artifact reference')


def main():
    global PREFIX
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=('prepare', 'dispatch', 'inspect'))
    p.add_argument('--folder', type=Path, required=True)
    args = p.parse_args(); folder = args.folder.resolve(); stage = folder / 'registered-batch-v1'
    PREFIX = installed_prefix(folder)
    _, image, _ = release.image_evidence(folder)
    plan = release.read(folder / 'deployment-v1', 'plan')
    completion = release.read(folder / 'deployment-v1', 'completion')
    protected(completion)
    require(completion['status'] == 'scoped_reporting_workers_deployed_and_identity_verified'
            and completion['image'] == image and plan['image'] == image
            and plan['controller_sha256'] == release.build.sha(Path(release.__file__)),
            'Installed qualified release required')
    jobs = release.all_jobs(folder / 'deployment-v1', plan)
    source, hashes = prepare_source(folder, plan)
    template = corrected_template(jobs[JOB], source)
    if args.stage == 'prepare':
        require(not stage.exists(), 'Preserve existing batch attempt')
        release.window()
        for n in release.build.JOBS: release.idle(n)
        no_matching_execution(JOB, [template['containers'][0]['args']])
        record = {'image': image, 'job': JOB, 'prefix': PREFIX, 'template': template,
                  'source_sha256': hashlib.sha256(source.encode()).hexdigest(), 'probe_hashes': hashes,
                  'controller_sha256': release.build.sha(Path(__file__)),
                  'deployment_completion_sha256': release.build.sha(folder / 'deployment-v1/completion.json'),
                  'database_writes': 0, 'website_batch_registered': False,
                  'authorization': 'Owner requested next private 21-month MS reporting batch; no approval or activation'}
        stage.mkdir()
        release.build.save(stage, 'render-intent', {**record, 'protected_receipt': retain(record, 'ms-installed-private-batch-v1-render-intent')})
        print(json.dumps({'status': 'prepared', 'months': 21, 'database_writes': 0}), flush=True)
        return
    intent = release.read(stage, 'render-intent'); protected(intent)
    require(intent['template'] == template and intent['probe_hashes'] == hashes
            and intent['source_sha256'] == hashlib.sha256(source.encode()).hexdigest()
            and intent['controller_sha256'] == release.build.sha(Path(__file__))
            and intent['deployment_completion_sha256'] == release.build.sha(folder / 'deployment-v1/completion.json'),
            'Frozen private render intent changed')
    if args.stage == 'dispatch':
        require(not (stage / 'render-dispatch-intent.json').exists(), 'Dispatch attempted; inspect without retry')
        release.window()
        for n in release.build.JOBS: release.idle(n)
        no_matching_execution(JOB, [template['containers'][0]['args']])
        release.build.save(stage, 'render-dispatch-intent', {'source_sha256': intent['source_sha256'], 'image': image})
        ack = request('POST', release.path(JOB) + '/start' + release.API, data=template)
        require(re.fullmatch(re.escape(JOB) + '-[a-z0-9]{7}', ack.get('name', '')), 'Unknown dispatch; inspect without retry')
        release.build.save(stage, 'render-dispatch', {'execution': ack['name'], 'image': image})
        print(json.dumps({'execution': ack['name'], 'months': 21, 'reports_released': False}), flush=True)
        return
    ack = release.read(stage, 'render-dispatch')
    run = request('GET', release.path(JOB) + '/executions/' + ack['execution'] + release.API)
    state = run['properties']['status']; print(json.dumps({'execution': ack['execution'], 'status': state}), flush=True)
    if state != 'Succeeded': return
    baseline_ack = release.read(folder / 'deployment-v1', JOB + '-check-qualified')
    baseline = request('GET', release.path(JOB) + '/executions/' + baseline_ack['execution'] + release.API)
    require(baseline['properties']['status'] == 'Succeeded', 'Qualified installed baseline unavailable')
    actual = run['properties']['template']['containers']
    require(len(actual) == 1, 'Unexpected executed container inventory')
    projection = verify_execution_projection(template['containers'][0], actual[0],
                   baseline['properties']['template']['containers'][0], jobs[JOB])
    env = {e['name']: e for e in template['containers'][0]['env']}
    service = BlobServiceClient(env['AZURE_STORAGE_ACCOUNT_URL']['value'], credential=AzureCliCredential())
    data = service.get_blob_client('internal-reports', PREFIX + 'render/verification.json').download_blob().readall()
    require(len(data) <= 16 * 1024 * 1024, 'Oversized runtime proof')
    proof = json.loads(data); prior = release.read(folder / 'runtime-v1', 'candidate-qualified')
    validate_proof(proof, intent, prior)
    preflight = json.loads(service.get_blob_client('internal-reports', PREFIX + 'preflight.json').download_blob().readall())
    require(preflight['status'] == 'passed' and preflight['database_writes'] == 0
            and len(preflight['months']) == 21
            and all(m['presentation_preflight']['status'] == 'pass' for m in preflight['months']), 'Private database preflight differs')
    output = stage / 'outputs'; output.mkdir(exist_ok=True)
    package_equal = 0
    for row in proof['artifacts']:
        for kind in ('factsheet', 'presentation'):
            name = row[kind + '_blob']; content = service.get_blob_client('internal-reports', name).download_blob().readall()
            require(hashlib.sha256(content).hexdigest() == row[kind + '_sha256'], 'Cloud output readback differs')
            target = output / Path(name).name
            if target.exists(): require(target.read_bytes() == content, 'Conflicting local output')
            else:
                with target.open('xb') as handle: handle.write(content)
            if kind == 'presentation':
                previous = folder / 'runtime-v1/candidate-outputs' / target.name
                if package_parts(target) == package_parts(previous): package_equal += 1
    record = {'status': 'installed_private_render_readback_passed', 'image': image,
              'execution': ack['execution'], 'proof': proof, 'preflight': preflight,
              'execution_projection': projection, 'proof_sha256': hashlib.sha256(data).hexdigest(),
              'pdfs_identical_to_accepted': 21, 'presentations_package_parts_identical': package_equal,
              'database_writes': 0, 'website_batch_registered': False, 'reports_released': False,
              'onboarding_activated': False, 'visual_acceptance': False}
    release.build.save(stage, 'render-qualified', {**record, 'protected_receipt': retain(record, 'ms-installed-private-batch-v1-render-qualified')})
    print(json.dumps({'status': record['status'], 'pdfs': 21, 'presentations': 21,
                      'presentations_package_parts_identical': package_equal, 'website_batch_registered': False}), flush=True)


if __name__ == '__main__': main()
