"""Render corrected private report versions in the deployed Azure renderer.

Reads correction inputs and current uploaded design authorities, with a
read-only database transaction. Never changes report release or onboarding.
"""
import hashlib
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path[:0] = ['/app/src', '/app/scripts']
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.monthly_artifacts import render_factsheet, ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION
from ibkr_etl.alpha_world_ms_pdf_validation import validate
from ibkr_etl.alpha_world_ms_analytics import analyze
from generate_month_end_package import _require_factsheet_identity

TENANT = '00000000-0000-0000-0000-000000000001'
PREFIX = 'review/alpha-world-ms/source-recovery-20261007/'
PERIODS = ('2026-08-31', '2026-09-30')
PHASE = 'start'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    global PHASE
    release = json.loads(Path('/app/ALPHA_MS_RENDER_RELEASE.json').read_bytes())
    for name, expected in release['installed_files_sha256'].items():
        if sha((Path('/app') / name).read_bytes()) != expected:
            raise ValueError('Installed reporting code differs from the qualified release')
    if release['layout_version'] != ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION:
        raise ValueError('Qualified layout version differs')
    service = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    def read(name):
        return service.get_blob_client('internal-reports', PREFIX + name).download_blob().readall()
    def put(name, data, content_type):
        blob = service.get_blob_client('internal-reports', PREFIX + 'corrected-reports/' + name)
        blob.upload_blob(data, overwrite=False, content_settings=ContentSettings(content_type=content_type),
            metadata={'sha256': sha(data), 'subject_id': 'alpha-world-ms', 'layout': 'v14r2'})
        if sha(blob.download_blob().readall()) != sha(data):
            raise ValueError('Private artifact readback differs')
    evidence = json.loads(read('derived-apply.json'))
    if evidence['status'] != 'derived_history_committed' or not evidence['broker_facts_unchanged']:
        raise ValueError('Validated correction evidence required')
    reports = {r['period_end']: r for r in evidence['reports']}
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
            options='-c default_transaction_read_only=on -c statement_timeout=120000', connect_timeout=25) as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        assets = c.execute('''SELECT asset_role,container_name,blob_name,sha256,approval_status
            FROM subject_report_template_assets WHERE tenant_id=%s AND subject_id='alpha-world-ms'
            AND report_type='factsheet' AND is_current''', (TENANT,)).fetchall()
        for period, record in reports.items():
            for parent in record['parent_snapshots']:
                row = c.execute('SELECT data_sha256,status FROM reporting_snapshots WHERE snapshot_id=%s', (parent['snapshot_id'],)).fetchone()
                if not row or row['data_sha256'] != parent['data_sha256'] or row['status'] != parent['status']:
                    raise ValueError('Previously frozen snapshot changed')
        c.rollback()
    roles = {a['asset_role']: a for a in assets}
    if len(assets) != 2 or set(roles) != {'template','completed_example'}:
        raise ValueError('Current owner-uploaded design pair required')
    if roles['completed_example']['sha256'] != 'f12d94ec0f18a757263d6956789c1ae4718b52362d77abd126ebc813ff464909':
        raise ValueError('MS design authority differs')
    artifacts = []
    with TemporaryDirectory(prefix='alpha-ms-corrected-') as folder:
        root = Path(folder)
        for role, asset in roles.items():
            data = service.get_blob_client(asset['container_name'], asset['blob_name']).download_blob().readall()
            if sha(data) != asset['sha256'] or asset['approval_status'] != 'validated':
                raise ValueError('Design asset identity or hash invalid')
            (root / (role + '.pdf')).write_bytes(data)
        for period in PERIODS:
            PHASE = 'render_' + period
            raw = read('corrected-inputs/' + period + '.json')
            if sha(raw) != reports[period]['data_sha256']:
                raise ValueError('Corrected input digest differs')
            payload = json.loads(raw)
            metrics, daily, analytics = analyze(payload)
            if any({'2026-08-27','2026-08-28'}.intersection(i['missing_sessions']) for i in analytics['excluded_intervals']):
                raise ValueError('Original source gap still present')
            name = 'AlphaWorld_MS_Factsheet_' + period + '_Recovered.pdf'
            target = root / name
            result = render_factsheet(payload, template_path=root / 'template.pdf',
                design_model_path=root / 'completed_example.pdf', output_path=target)
            if result['layout_version'] != ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION:
                raise ValueError('Installed layout version differs')
            _require_factsheet_identity(target, 'iStrategy: Alpha World MS', subject_id='alpha-world-ms')
            accuracy = validate(target, payload)
            data = target.read_bytes()
            put(name, data, 'application/pdf')
            artifacts.append({'period_end': period, 'blob_name': PREFIX + 'corrected-reports/' + name,
                'sha256': sha(data), 'data_sha256': sha(raw), 'render': result,
                'accuracy': accuracy, 'analytics': analytics, 'metrics': metrics})
    manifest = {'status': 'corrected_private_factsheets_rendered_in_azure',
        'subject_id': 'alpha-world-ms', 'layout_version': ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION,
        'installed_files_sha256': release['installed_files_sha256'],
        'artifacts': artifacts, 'assets': assets, 'reports_released': False,
        'published_snapshots_changed': False, 'onboarding_activated': False}
    put('manifest.json', json.dumps(manifest, sort_keys=True, indent=2).encode(), 'application/json')
    print('ALPHA_MS_RECOVERY_RESULT ' + json.dumps({k:v for k,v in manifest.items() if k not in ('artifacts','assets')}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print('ALPHA_MS_RECOVERY_RESULT ' + json.dumps({'status':'stopped','phase':PHASE,
            'error_class':type(e).__name__, 'error':str(e) if isinstance(e,ValueError)
            else 'Service details retained privately; credentials redacted'}), flush=True)
        raise SystemExit(1)
