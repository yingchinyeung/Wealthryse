"""Read-only preflight for the owner-authorized private MS batch regeneration."""
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path[:0] = ['/app/src', '/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.subject_read_contract import require_ms_hkd_contract
from ibkr_etl.alpha_world_ms_analytics import analyze
from ibkr_etl.onboarding_reports import template_evidence
from ibkr_etl.presentation_design import platform_content_expectations
from render_onboarding_historical_reports import _expected_strategy

TENANT = '00000000-0000-0000-0000-000000000001'
SUBJECT = 'alpha-world-ms'


def main():
    service = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=_hybrid_row_factory,
            connect_timeout=25, options='-c default_transaction_read_only=on -c statement_timeout=120000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        c = PostgresConnection(raw)
        require_ms_hkd_contract(c, tenant_id=TENANT)
        subject = dict(c.execute('SELECT base_currency,reporting_currency,lifecycle_status FROM platform_subjects WHERE subject_id=?', (SUBJECT,)).fetchone())
        if subject != {'base_currency':'HKD','reporting_currency':'HKD','lifecycle_status':'onboarding'}:
            raise ValueError('Unexpected subject currency/lifecycle')
        assets = [dict(r) for r in c.execute('SELECT report_type,asset_role,version_label,container_name,blob_name,content_type,sha256,byte_count,approval_status,metadata_json,is_current FROM subject_report_template_assets WHERE subject_id=? AND is_current', (SUBJECT,))]
        evidence = template_evidence(assets, subject_id=SUBJECT)
        presentation = next(r for r in assets if r['report_type']=='presentation' and r['asset_role']=='template')
        strategy = _expected_strategy(c, SUBJECT, presentation['sha256'])
        months = []
        for row in c.execute("SELECT snapshot_id,period_end,data_sha256,quality_status,publication_allowed,snapshot_payload_json FROM reporting_snapshots WHERE subject_id=? AND status IN ('frozen','approved') ORDER BY period_end", (SUBJECT,)):
            payload_text = str(row['snapshot_payload_json'])
            if hashlib.sha256(payload_text.encode()).hexdigest()!=row['data_sha256']:
                raise ValueError('Snapshot content hash differs')
            payload = json.loads(payload_text)
            if payload['reporting_currency']!='HKD' or row['quality_status']=='fail' or row['publication_allowed']:
                raise ValueError('Snapshot currency, quality or publication boundary differs')
            metrics,_,calculation = analyze(payload)
            try:
                expected = platform_content_expectations(payload)
                deck = {'status':'pass', 'expected_slides':sorted(expected['slide_text'])}
            except Exception as error:
                deck = {'status':'blocked','error_class':type(error).__name__,'error':str(error)[:600]}
            months.append({'period_end':str(row['period_end']),'snapshot_id':str(row['snapshot_id']),
                'data_sha256':row['data_sha256'],'metrics':metrics,'calculation':calculation,
                'presentation_preflight':deck,'monthly_reporting_available':(payload.get('monthly_reporting') or {}).get('available'),
                'benchmark_available':(payload.get('benchmark') or {}).get('available'),
                'crisis_available':(payload.get('crisis') or {}).get('available')})
        if len(months)!=21 or len({r['period_end'] for r in months})!=21:
            raise ValueError('Exactly 21 distinct active historical snapshots required')
        batches=[dict(r) for r in c.execute('SELECT batch_id,status,template_evidence_sha256,manifest_sha256,expected_month_count,expected_artifact_count FROM onboarding_historical_report_batches WHERE subject_id=? ORDER BY requested_at DESC', (SUBJECT,))]
        raw.rollback()
    proof={'status':'passed','subject':subject,'assets':assets,'template_evidence':evidence,
        'presentation_strategy':strategy,'months':months,'batches':batches,'database_writes':0,
        'reports_released':False,'onboarding_activated':False,'ibkr_source_changed':False}
    content=json.dumps(proof,sort_keys=True,indent=2,default=str,allow_nan=False).encode()
    blob=os.environ['MS_BATCH_PREFLIGHT_BLOB']
    service.get_blob_client('internal-reports',blob).upload_blob(content,overwrite=False,
        content_settings=ContentSettings(content_type='application/json'),metadata={'sha256':hashlib.sha256(content).hexdigest()})
    print(json.dumps({'status':'passed','months':len(months),'blob':blob,'sha256':hashlib.sha256(content).hexdigest()}),flush=True)


if __name__=='__main__':main()
