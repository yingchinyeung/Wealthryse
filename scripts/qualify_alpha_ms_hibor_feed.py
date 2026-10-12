"""One-off Azure HIBOR rehearsal: official HTTP + in-memory SQLite only.

Never connects to DATABASE_URL, changes a job, runs migrations, calls Flex,
publishes reports or alters the licensed historical bootstrap.
"""
import base64
from datetime import date, datetime, timezone
import hashlib
import json
import os
import sqlite3
import sys
import types
import zlib

sys.path[:0] = ['/app/src','/app/scripts']
import ibkr_etl


def main():
    # The execution override explicitly removes production DB access from this
    # process. Managed identity is used only for private evidence storage.
    if os.environ.get('DATABASE_URL'):
        raise ValueError('Isolated HIBOR rehearsal must not receive DATABASE_URL')
    if os.environ.get('HIBOR_LICENSE_SCOPE_CONFIRMED') != 'true':
        raise ValueError('Owner-confirmed HIBOR usage scope required')
    compressed = os.environ['ALPHA_MS_HIBOR_CODE_BUNDLE']
    raw_bundle = zlib.decompress(base64.b64decode(compressed))
    if hashlib.sha256(raw_bundle).hexdigest() != os.environ['ALPHA_MS_HIBOR_CODE_SHA256']:
        raise ValueError('Frozen qualification source differs')
    bundle = json.loads(raw_bundle)
    hashes = {}
    for name in ('market_calendar','hibor'):
        source = bundle[name]
        full_name = 'ibkr_etl.'+name
        module = types.ModuleType(full_name)
        module.__file__ = '/isolated-qualification/'+name+'.py'
        sys.modules[full_name] = module
        setattr(ibkr_etl,name,module)
        exec(compile(source,module.__file__,'exec'),module.__dict__)
        hashes[name] = hashlib.sha256(source.encode()).hexdigest()
    from ibkr_etl.hibor import period_average, refresh, latest_expected_fixing
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient, ContentSettings

    storage = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(
        exclude_interactive_browser_credential=True))
    prefix = 'review/alpha-world-ms/hkd-hibor-v14r8-20261007/inputs/'
    historical_bytes = storage.get_blob_client('internal-reports',prefix+'official-hibor.json').download_blob().readall()
    bootstrap_sha = '9bef4e571e2564c5f0d2a462882ab29785d098df9397edc63b584830c6a52edf'
    if hashlib.sha256(historical_bytes).hexdigest() != bootstrap_sha:
        raise ValueError('Licensed historical bootstrap integrity differs')
    historical = json.loads(historical_bytes)
    original_average = period_average(historical,date(2025,1,20),date(2026,9,30))
    policy = latest_expected_fixing(now=datetime.now(timezone.utc))
    with sqlite3.connect(':memory:') as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('''CREATE TABLE risk_free_rates (
            tenant_id TEXT NOT NULL DEFAULT 'isolated-qualification', rate_date TEXT NOT NULL,
            series_id TEXT NOT NULL, currency TEXT NOT NULL, tenor TEXT NOT NULL,
            annual_rate REAL NOT NULL, day_count_convention TEXT NOT NULL,
            provider TEXT NOT NULL, fetched_at TEXT NOT NULL, quality_status TEXT NOT NULL,
            PRIMARY KEY (tenant_id,rate_date,series_id))''')
        connection.executemany("INSERT INTO risk_free_rates(rate_date,series_id,currency,tenor,annual_rate,day_count_convention,provider,fetched_at,quality_status) "
            "VALUES (?,'HKAB-HIBOR-1M','HKD','1-month',?,'annualized-money-market-fixing','licensed protected bootstrap','2026-10-07','pass')",
            [(r['rate_date'],r['annual_rate']) for r in historical])
        connection.commit()
        result = refresh(connection,date(2025,1,20),date.fromisoformat(policy['expected_date']),license_scope_confirmed=True)
        observed = set(result['transport']['observed_dates'])
        if result['observations_loaded'] != len(observed):
            raise ValueError('Refresh count differs from actually observed dates')
        retained = list(connection.execute('SELECT rate_date,provider,fetched_at FROM risk_free_rates'))
        if any(r['provider'] != 'licensed protected bootstrap' or r['fetched_at'] != '2026-10-07'
               for r in retained if r['rate_date'] not in observed):
            raise ValueError('Retained history lost its original provenance')
        rows = [dict(r) for r in connection.execute('SELECT rate_date,annual_rate FROM risk_free_rates ORDER BY rate_date')]
        new_average = period_average(rows,date(2025,1,20),date(2026,9,30))
        if new_average['source_value_sha256'] != original_average['source_value_sha256']:
            raise ValueError('Re-observed historical HIBOR changed; independent correction review required')
        if rows[-1]['rate_date'] != policy['expected_date']:
            raise ValueError('Latest fixing does not meet the publication policy')
        # Reapplying the validated rows tests the same upsert without a second
        # HTTP call: no duplicate source observations or immediate retries.
        from unittest.mock import patch
        before = len(rows)
        with patch('ibkr_etl.hibor.fetch',return_value=rows):
            refresh(connection,date(2025,1,20),date.fromisoformat(policy['expected_date']),license_scope_confirmed=True)
        if connection.execute('SELECT COUNT(*) FROM risk_free_rates').fetchone()[0] != before:
            raise ValueError('Idempotency rehearsal inserted duplicate fixings')
    evidence = {'status':'qualified','captured_at':datetime.now(timezone.utc).isoformat(),
        'code_bundle_sha256':hashlib.sha256(raw_bundle).hexdigest(),'module_sha256':hashes,
        'historical_bootstrap_sha256':bootstrap_sha,'historical_fixings_verified':len(historical),
        'historical_average_unchanged':True,'publication_policy':policy,'refresh_result':result,
        'retained_provenance':'pass',
        'observations':rows,'isolated_database':'in-memory SQLite','idempotency':'pass',
        'production_database_connected':False,'production_database_changed':False,
        'ibkr_source_changed':False,'persistent_job_changed':False,'reports_released':False}
    data = json.dumps(evidence,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    digest = hashlib.sha256(data).hexdigest()
    name = 'review/alpha-world-ms/hibor-step1-20261007/qualification-'+digest+'.json'
    blob = storage.get_blob_client('internal-reports',name)
    if not blob.exists():
        blob.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type='application/json'),metadata={'sha256':digest})
    if hashlib.sha256(blob.download_blob().readall()).hexdigest() != digest:
        raise ValueError('Private qualification evidence readback differs')
    print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'qualified','blob':name,'sha256':digest,
        'latest_fixing':rows[-1],'fixings':len(rows),'transport':result['transport'],
        'production_database_changed':False,'ibkr_source_changed':False}),flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'stopped','error_class':type(error).__name__,
            'error':str(error) if isinstance(error,(ValueError,KeyError,TypeError)) else 'Service details redacted'}),flush=True)
        raise SystemExit(1)
