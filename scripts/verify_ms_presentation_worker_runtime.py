"""Injected read-only MS runtime probe; bindings are supplied by the controller.

Only private create-only blob output is written. PostgreSQL enforces READ ONLY.
The existing production worker entrypoints are imported but never invoked.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path[:0] = ['/app/src', '/app/scripts']
import psycopg
from azure.core.exceptions import ResourceExistsError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.util import canonical_json
from ibkr_etl.subject_read_contract import require_ms_hkd_contract
from ibkr_etl.alpha_world_ms_hkd import load_risk_snapshot
from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id
from ibkr_etl.monthly_artifacts import render_factsheet, factsheet_layout_version
from ibkr_etl.alpha_world_ms_pdf_validation import validate
from ibkr_etl.alpha_world_ms_copy import validate_copy
from generate_month_end_package import _require_factsheet_identity, _load_frozen_snapshot
from render_onboarding_historical_reports import _expected_strategy

TENANT = '00000000-0000-0000-0000-000000000001'
SUBJECT = 'alpha-world-ms'
PLATFORM_TEMPLATE = Path('/app/templates/U16088571/approved-august-2026-20-slide-v24.pptx')
TEMPLATE_SHA = '4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191'
COMMIT_BLOB = ('review/alpha-world-ms/hkd-cutover-v14r11-20261007/'
               'database-cutover-06c44ff756bbc7da0f41f14c9bfe08fff2ecb16e06a94c8576654bf176a00038.json')
EXPECTED_LAYOUT = 'subject-completed-factsheet-model-v14r13-alpha-world-ms-distribution-quarter-ticks'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    binding = PROBE_BINDING  # injected immutable intent, not user environment
    mode = binding['mode']
    if mode not in ('candidate', 'rollback') or len(binding['periods']) != 21:
        raise ValueError('Exact 21-month candidate/rollback probe required')
    if factsheet_layout_version(SUBJECT) != EXPECTED_LAYOUT:
        raise ValueError('Factsheet layout changed unexpectedly')
    if mode == 'candidate':
        release = json.loads(Path('/app/MS_PRESENTATION_WORKER_RELEASE.json').read_bytes())
        if (release['content_contract_version'] != binding['content_contract_version']
                or binding['content_contract_version'] != 'alpha-world-ms-v24-hkd-hibor-evidence-v2'
                or release.get('chart_workbooks_required') is not True
                or release['report_design_updates_included'] or release['website_cutover']):
            raise ValueError('Unexpected scoped candidate release')
        for name, digest in binding['installed_files_sha256'].items():
            if sha(Path('/app', name).read_bytes()) != digest:
                raise ValueError('Candidate installed source differs')
        import generate_month_end_package as monthly
        import render_onboarding_historical_reports as historical
        from validate_subject_presentation import validate_subject_presentation
        if (monthly.validate_subject_presentation is not validate_subject_presentation
                or historical.validate_subject_presentation is not validate_subject_presentation):
            raise ValueError('Shared worker gate not connected')
    if sha(PLATFORM_TEMPLATE.read_bytes()) != TEMPLATE_SHA:
        raise ValueError('Installed approved platform template changed')
    service = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))

    def put(name, content, kind):
        blob_name = binding['prefix'] + name
        blob = service.get_blob_client('internal-reports', blob_name)
        try:
            blob.upload_blob(content, overwrite=False, content_settings=ContentSettings(content_type=kind),
                metadata={'sha256': sha(content), 'subject_id': SUBJECT})
        except ResourceExistsError:
            if blob.download_blob().readall() != content:
                raise ValueError('Conflicting private probe output')
        if blob.download_blob().readall() != content:
            raise ValueError('Private probe readback differs')
        return blob_name

    commit = json.loads(service.get_blob_client('internal-reports', COMMIT_BLOB).download_blob().readall())
    if commit['status'] != 'committed' or commit['image_digest'] != 'sha256:06c44ff756bbc7da0f41f14c9bfe08fff2ecb16e06a94c8576654bf176a00038':
        raise ValueError('Immutable HKD database commit evidence differs')
    reports = {r['period_end']: r for r in commit['reports']}
    if set(reports) != set(binding['periods']):
        raise ValueError('Historical period coverage differs')
    artifacts = []
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=_hybrid_row_factory,
        connect_timeout=25, options='-c default_transaction_read_only=on -c statement_timeout=120000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        if raw.execute('SHOW transaction_read_only').fetchone()[0] != 'on':
            raise ValueError('Database read-only enforcement missing')
        c = PostgresConnection(raw)
        require_ms_hkd_contract(c, tenant_id=TENANT)
        subject = dict(c.execute('SELECT base_currency,reporting_currency,lifecycle_status FROM platform_subjects WHERE tenant_id=? AND subject_id=?', (TENANT, SUBJECT)).fetchone())
        if subject != {'base_currency': 'HKD', 'reporting_currency': 'HKD', 'lifecycle_status': 'onboarding'}:
            raise ValueError('Live MS lifecycle or currency drifted')
        presentation_asset = c.execute("SELECT sha256,container_name,blob_name FROM subject_report_template_assets WHERE tenant_id=? AND subject_id=? AND report_type='presentation' AND asset_role='template' AND is_current AND approval_status='validated'", (TENANT, SUBJECT)).fetchall()
        if len(presentation_asset) != 1:
            raise ValueError('Exactly one validated presentation identity asset required')
        asset = presentation_asset[0]
        strategy = _expected_strategy(c, SUBJECT, str(asset['sha256']))
        if strategy != 'iStrategy: Alpha World MS':
            raise ValueError('Current owner-approved presentation identity differs')
        original_identity = service.get_blob_client(asset['container_name'], asset['blob_name']).download_blob().readall()
        if sha(original_identity) != str(asset['sha256']):
            raise ValueError('Approved identity source bytes differ')
        assets = [dict(r) for r in c.execute("SELECT asset_role,container_name,blob_name,sha256 FROM subject_report_template_assets WHERE tenant_id=? AND subject_id=? AND report_type='factsheet' AND is_current AND approval_status='validated'", (TENANT, SUBJECT))]
        if len(assets) != 2 or {r['asset_role'] for r in assets} != {'template', 'completed_example'}:
            raise ValueError('Approved factsheet asset pair required')
        with TemporaryDirectory(prefix='ms-worker-runtime-') as directory:
            folder = Path(directory)
            for asset in assets:
                content = service.get_blob_client(asset['container_name'], asset['blob_name']).download_blob().readall()
                if sha(content) != asset['sha256']:
                    raise ValueError('Approved factsheet bytes differ')
                (folder / (asset['asset_role'] + '.pdf')).write_bytes(content)
            for period, accepted in sorted(binding['periods'].items()):
                committed = reports[period]
                snapshot_id = active_month_end_snapshot_id(c, tenant_id=TENANT, subject_id=SUBJECT, period_end=period)
                payload, digest = _load_frozen_snapshot(c, tenant_id=TENANT, account_id=SUBJECT, snapshot_id=snapshot_id)
                if (snapshot_id != committed['snapshot_id'] or digest != committed['data_sha256']
                        or sha(canonical_json(payload).encode()) != accepted['canonical_payload_sha256']
                        or payload['reporting_currency'] != 'HKD' or payload['publication_allowed']):
                    raise ValueError('Live frozen input differs from accepted private input: ' + period)
                _, metrics, _ = load_risk_snapshot(c, period, tenant_id=TENANT)
                if metrics != committed['metrics'] or payload['risk']['metrics'] != metrics:
                    raise ValueError('Live risk metrics differ: ' + period)
                snapshot = folder / (period + '.json')
                snapshot.write_text(canonical_json(payload), encoding='utf-8')
                pdf = folder / ('AlphaWorld_MS_Factsheet_' + period + '.pdf')
                render_factsheet(payload, template_path=folder / 'template.pdf',
                    design_model_path=folder / 'completed_example.pdf', output_path=pdf)
                _require_factsheet_identity(pdf, strategy, subject_id=SUBJECT)
                validate(pdf, payload)
                validate_copy(pdf)
                data = pdf.read_bytes()
                row = {'period_end': period, 'snapshot_id': snapshot_id, 'data_sha256': digest,
                       'factsheet_sha256': sha(data), 'factsheet_blob': put('outputs/' + pdf.name, data, 'application/pdf')}
                if mode == 'candidate':
                    from render_onboarding_historical_reports import _render_presentation
                    pptx = folder / ('AlphaWorld_MS_Presentation_' + period + '.pptx')
                    validation = _render_presentation(template=PLATFORM_TEMPLATE, snapshot=snapshot,
                        output=pptx, period_end=period, subject_id=SUBJECT, expected_strategy=strategy)
                    data = pptx.read_bytes()
                    row.update(presentation_sha256=sha(data),
                        presentation_blob=put('outputs/' + pptx.name, data, 'application/vnd.openxmlformats-officedocument.presentationml.presentation'),
                        financial_charts_verified=validation['ms_financial_audit']['financial_charts_verified'],
                        risk_metrics_verified=validation['ms_financial_audit']['risk_metrics_verified'],
                        embedded_workbooks_verified=validation['ms_chart_workbook_audit']['embedded_workbooks_verified'],
                        presentation_validation=validation)
                artifacts.append(row)
                print(json.dumps({'mode': mode, 'period': period, 'runtime_checks': 'passed'}), flush=True)
        raw.rollback()
    proof = {'status': 'passed', 'mode': mode, 'image': binding['image'], 'count': len(artifacts),
        'artifacts': artifacts, 'subject': subject, 'owner_identity_verified': True,
        'read_only_transaction_enforced': True, 'database_writes': 0, 'source_data_changed': False,
        'reports_released': False, 'onboarding_activated': False, 'layout_version': EXPECTED_LAYOUT,
        'visual_acceptance': False, 'data_source': 'Actual persistent frozen HKD snapshots and caches'}
    data = json.dumps(proof, sort_keys=True, indent=2, allow_nan=False).encode()
    blob_name = put('verification.json', data, 'application/json')
    print('MS_PRESENTATION_RUNTIME_RESULT ' + json.dumps({'status': 'passed', 'mode': mode,
        'months': len(artifacts), 'proof_blob': blob_name, 'proof_sha256': sha(data)}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('MS_PRESENTATION_RUNTIME_RESULT ' + json.dumps({'status': 'stopped',
            'error_class': type(error).__name__, 'error': str(error) if isinstance(error, ValueError)
            else 'Service details redacted'}), flush=True)
        raise SystemExit(1)
