"""Verify real Chrome downloads and seal the scoped MS header release evidence.

Never changes production data, approvals, resources or report records.
"""
import json
from pathlib import Path
import shutil
import sys
from zipfile import ZipFile, ZIP_DEFLATED

from pypdf import PdfReader
from ibkr_etl.alpha_world_ms_pdf_validation import validate_date_headers
from ibkr_etl.alpha_world_ms_subtitle import validate as validate_subtitle
import build_ms_presentation_worker_candidate as build
import release_alpha_ms_headers_v14r15 as candidate
import deploy_ms_headers_v14r15 as deployed
import deploy_ms_presentation_worker_release as jobs
from deploy_metadata_monitor import request
from run_specialist_gateway_read_verification import retain

FOLDER = candidate.FOLDER
OUTPUT = build.ROOT / 'output/pdf/AlphaWorld_MS_Azure_Headers_v14r15'
API = '?api-version=2024-03-01'


def read(path):
    return json.loads(path.read_bytes())


def require(value, message):
    if not value:
        raise ValueError(message)


def qualify_browser():
    observation = read(FOLDER / 'browser-observation.json')
    registered = read(FOLDER / 'registration-v1/register/result.json')
    accepted = read(FOLDER / 'cloud-acceptance.json')
    wanted = {r['period_end']: r for r in registered['factsheets']}
    cloud = {r['period_end']: r['pdf_sha256'] for r in accepted['checks'] if r['kind'] == 'worker'}
    downloads = observation['downloads']
    require(len(downloads) == len(wanted) == len(cloud) == 21, 'Exactly 21 real downloads required')
    require({r['period_end'] for r in downloads} == set(wanted), 'Downloaded month coverage differs')
    require(observation['browser'] == 'Chrome' and observation['authentication'] == 'existing owner session'
            and observation['ui_ready_months'] == 21 and observation['ui_released_months'] == 0
            and observation['approval_changes_selected'] is False, 'Owner-review UI boundary differs')
    checks = []
    sources = []
    for row in downloads:
        period = row['period_end']
        registered_row = wanted[period]
        path = Path(observation['download_directory']) / row['filename']
        require(path.name == row['filename'] and path.parent == Path(observation['download_directory']),
                'Download must stay in the observed directory')
        require(row['generation_run_id'] == registered_row['generation_run_id'], 'Website linked the wrong generation')
        require(path.name == Path(registered_row['blob_name']).name, 'Observed filename differs')
        digest = build.sha(path)
        require(digest == registered_row['sha256'] == cloud[period], 'Downloaded bytes differ from accepted Azure PDF')
        pdf = PdfReader(path)
        require(len(pdf.pages) == 2, 'Unexpected page count')
        validate_subtitle(pdf.pages[0])
        validate_date_headers(pdf, period)
        checks.append({'period_end': period, 'generation_run_id': row['generation_run_id'],
                       'pdf_sha256': digest, 'accepted_cloud_bytes_identical': True,
                       'subtitle_and_date_headers_passed': True})
        sources.append((period, path))
    # Create-only artifact export; keep all downloaded/previous files intact.
    require(not OUTPUT.exists() and not OUTPUT.with_suffix('.zip').exists(), 'Export exists; never overwrite')
    OUTPUT.mkdir()
    for period, path in sources:
        shutil.copyfile(path, OUTPUT / ('AlphaWorld_MS_Factsheet_' + period + '.pdf'))
    record = {'status': 'passed', 'count': 21, 'browser': 'Chrome', 'authenticated_owner_downloads': True,
              'observation_sha256': build.sha(FOLDER / 'browser-observation.json'),
              'registered_result_sha256': build.sha(FOLDER / 'registration-v1/register/result.json'),
              'checks': sorted(checks, key=lambda r: r['period_end']),
              'report_approvals_changed': False, 'reports_released': False}
    build.save(OUTPUT, 'verification', record)
    with ZipFile(OUTPUT.with_suffix('.zip'), 'x', compression=ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.iterdir()):
            archive.write(path, path.name)
    with ZipFile(OUTPUT.with_suffix('.zip')) as archive:
        require(archive.testzip() is None and len(archive.namelist()) == 22, 'Export archive failed')
        for check in checks:
            import hashlib
            content = archive.read('AlphaWorld_MS_Factsheet_' + check['period_end'] + '.pdf')
            require(hashlib.sha256(content).hexdigest() == check['pdf_sha256'], 'Archive bytes differ')
    build.save(FOLDER, 'browser-download-qualified', {**record, 'export_directory': str(OUTPUT),
               'archive': str(OUTPUT.with_suffix('.zip')), 'archive_sha256': build.sha(OUTPUT.with_suffix('.zip'))})
    print(json.dumps({'status': 'passed', 'real_website_downloads': 21, 'archive': str(OUTPUT.with_suffix('.zip'))}))


def seal():
    deployed.gates()
    completion = read(deployed.FOLDER / 'completion.json')
    deployed.verify_protected(completion)
    require(completion['status'] == 'scoped_headers_deployed', 'Deployment not qualified')
    plan = read(deployed.FOLDER / 'plan.json')
    deployed.verify_protected(plan)
    for name, target in plan['targets'].items():
        proof = read(deployed.FOLDER / (name + '-qualified.json'))
        deployed.verify_protected(proof)
        current = request('GET', target['path'] + API)
        require(current['properties']['template']['containers'][0]['image'] == target['image']
                and current['properties']['provisioningState'] == 'Succeeded', 'Installed image/state drift')
        invariant = deployed.web_hash(current) if target['kind'] == 'web' else jobs.settings_invariant(current)
        require(invariant == target['invariant'], 'Unrelated settings drift')
        if target['kind'] == 'web':
            revision = current['properties']['latestReadyRevisionName']
            require(revision == 'ibkrv2stg-web--ms-headers-v14r15', 'Website revision drift')
            running = request('GET', target['path'] + '/revisions/' + revision + API)['properties']
            require(running['healthState'] == 'Healthy' and running['active'], 'Website not healthy')
    require(request('GET', deployed.WEB + '/authConfigs/current' + API)['properties'] == plan['auth_before']['properties'],
            'Authentication drift')
    evidence_paths = ['cloud-acceptance.json', 'deployment-v1/completion.json',
                      'browser-observation.json', 'browser-download-qualified.json']
    for mode in ('baseline', 'register', 'verify'):
        folder = FOLDER / 'registration-v1' / mode
        qualified = read(folder / 'qualified.json')
        deployed.verify_protected(qualified)
        require(qualified['status'] == 'passed' and build.sha(folder / 'result.json') == qualified['result_sha256'],
                'Protected registration proof differs')
        evidence_paths += ['registration-v1/' + mode + '/' + n for n in ('qualified.json', 'result.json')]
    verification = read(FOLDER / 'registration-v1/verify/result.json')
    require(verification['status'] == 'registered_factsheets_verified' and verification['count'] == 21
            and not any(verification[k] for k in ('source_data_changed', 'approvals_changed', 'reports_released', 'onboarding_changed')),
            'Final registration scope verification failed')
    browser = read(FOLDER / 'browser-download-qualified.json')
    require(browser['status'] == 'passed' and browser['count'] == 21
            and build.sha(Path(browser['archive'])) == browser['archive_sha256'], 'Website export proof changed')
    future = verification['future_reporting']
    require(future['status'] == 'passed' and future['active_month_end_registrations'] == 1
            and future['layout_version'] == candidate.LAYOUT and future['shared_historical_and_month_end_renderer']
            and future['source_quality_and_approval_gates_preserved'], 'Future renderer configuration failed')
    record = {'status': 'scoped_ms_headers_release_complete', 'layout_version': candidate.LAYOUT,
              'targets': completion['targets'], 'historical_months_regenerated': 21,
              'actual_authenticated_website_downloads_verified': 21, 'future_reporting': future,
              'factsheet_generation_runs_created': 21, 'audit_events_created': 1,
              'source_data_changed': False, 'calculations_changed': False, 'report_approvals_changed': False,
              'reports_released': False, 'presentations_changed': False, 'alpha_world_t_changed': False,
              'onboarding_changed': False, 'previous_report_records_and_files_preserved': True,
              'authentication_and_other_settings_preserved': True, 'rollback_runtime_qualified': True,
              'evidence_files_sha256': {name: build.sha(FOLDER / name) for name in evidence_paths},
              'export_archive_sha256': browser['archive_sha256'],
              'qualification_limit': 'Future job entrypoints and active registration verified; no future observation fabricated or normal month-end run forced.'}
    build.save(FOLDER, 'final-completion', {**record, 'protected_receipt': retain(record, 'ms-headers-v14r15-final-completion')})
    print(json.dumps({'status': record['status'], 'months': 21, 'website_downloads': 21, 'reports_released': False}))


if __name__ == '__main__':
    {'browser': qualify_browser, 'seal': seal}[sys.argv[1]]()
