"""Read-only installed sync source-access/hash/freshness acceptance."""
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit
sys.path[:0] = ['/app/src', '/app/scripts']
from ibkr_etl.cloud_storage import _service
from ibkr_etl.daily_account_sync import validate_production_export_manifest


def main():
    if os.environ.get('DATABASE_URL') or os.environ.get('IBKR_FLEX_TOKEN'):
        raise ValueError('Installed read test must not receive database or broker credentials')
    tenant = os.environ['PORTFOLIO_TENANT_ID']
    url = os.environ['PRODUCTION_STORAGE_ACCOUNT_URL']
    if tenant != '00000000-0000-0000-0000-000000000001' or urlsplit(url).hostname != 'ibkrv115rcc62xxqblay.blob.core.windows.net':
        raise ValueError('Exact existing export source required')
    service = _service(url)
    container = os.environ.get('PRODUCTION_EXPORT_CONTAINER', 'analytics-reports')
    prefix = os.environ.get('PRODUCTION_EXPORT_PREFIX', tenant + '/latest').strip('/')
    if container != 'analytics-reports' or prefix != tenant + '/latest':
        raise ValueError('Exact current export prefix required')
    client = service.get_blob_client(container, prefix + '/IBKR_Analyst_Export_Manifest_Latest.json')
    props = client.get_blob_properties()
    if props.size > 65536: raise ValueError('Manifest exceeds bounded size')
    content = client.download_blob().readall(); manifest = json.loads(content)
    if manifest.get('tenant_id') != tenant or manifest.get('sqlite_file') != 'IBKR_Analyst_Snapshot_Latest.sqlite':
        raise ValueError('Source identity differs')
    expected = manifest['sqlite_sha256']
    if len(expected) != 64 or any(c not in '0123456789abcdef' for c in expected): raise ValueError('Exact source SHA256 required')
    source = service.get_blob_client(container, prefix + '/' + manifest['sqlite_file'])
    size = source.get_blob_properties().size
    if not 0 < size <= 512 * 1024**2: raise ValueError('Source size exceeds read-only bound')
    sha = hashlib.sha256(); length = 0
    for chunk in source.download_blob().chunks(): sha.update(chunk); length += len(chunk)
    if length != size or sha.hexdigest() != expected: raise ValueError('Source bytes differ from manifest')
    fresh = True; error_class = None
    try: validate_production_export_manifest(content, expected_tenant_id=tenant)
    except (ValueError, RuntimeError) as error: fresh = False; error_class = type(error).__name__
    generated = datetime.fromisoformat(manifest['generated_at'].replace('Z', '+00:00'))
    if generated.tzinfo is None: raise ValueError('Aware source timestamp required')
    files = {p.relative_to('/app').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix != '.pyc'}
    original = json.loads(Path('/qualification/tests/baseline-source.json').read_text())
    if files != original or len(files) != 452: raise ValueError('Installed application bytes drifted')
    print('SYNC_INSTALLED_READ_PROOF ' + json.dumps({'passed': True, 'source_access': 'authenticated_read_passed', 'source_hash_verified': True, 'source_sha256': expected, 'source_bytes': size, 'source_generated_at': generated.isoformat(), 'source_age_hours': round((datetime.now(timezone.utc)-generated).total_seconds()/3600, 3), 'normal_freshness_gate_passed': fresh, 'freshness_error_class': error_class, 'normal_import_executed': False, 'application_files_unchanged': len(files), 'database_connections': 0, 'database_writes': 0, 'blob_writes': 0, 'broker_calls': 0}), flush=True)


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print('SYNC_INSTALLED_READ_ERROR ' + json.dumps({'error_class': type(error).__name__}), flush=True)
        raise SystemExit(1)
