"""Eight-file isolated CLI context; freeze source, model metadata and helpers."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT/'.work/codex-exec-runtime-20261003'
PATHS = {
    'codex-runtime.lock.json': 'deploy/codex-runtime.lock.json',
    'Dockerfile': 'deploy/codex-exec-probe.Dockerfile',
    'src/ibkr_etl/codex_runtime_probe.py': 'src/ibkr_etl/codex_runtime_probe.py',
    'src/ibkr_etl/codex_exec_probe.py': 'src/ibkr_etl/codex_exec_probe.py',
    'scripts/install_pinned_codex_runtime.py': 'scripts/install_pinned_codex_runtime.py',
    'scripts/run_codex_exec_probe.py': 'scripts/run_codex_exec_probe.py',
    'codex-exec-model-catalog.json': 'deploy/codex-exec-model-catalog.json',
    'catalog-source.json': 'deploy/codex-exec-catalog-source.json',
}
HELPERS = (
    'scripts/deploy_codex_exec_probe.py', 'scripts/deploy_codex_runtime_probe.py',
    'scripts/prepare_codex_exec_probe.py', 'scripts/audit_azure_runtime.py',
    'scripts/extract_codex_exec_catalog.py',
    'src/ibkr_etl/specialist_archive_reader.py', 'src/ibkr_etl/specialist_audit_service.py',
    'scripts/deploy_metadata_monitor.py', 'scripts/deploy_specialist_daily_schedule.py',
    'scripts/run_specialist_gateway_read_verification.py', 'scripts/run_codex_identity_trial.py',
)


def manifest():
    files = {name: (ROOT/path).read_bytes() for name, path in PATHS.items()}
    catalog = json.loads(files['codex-exec-model-catalog.json'])
    descriptor = json.loads(files['catalog-source.json'])
    if (hashlib.sha256(files['codex-exec-model-catalog.json']).hexdigest() != descriptor['catalog_sha256'] or
            descriptor['catalog_sha256'] != '0c231c9cce20499aabf693206989a84cb0552d83bb6af80ec1ea471bfeb282bb' or
            descriptor['native_entry_sha256'] != '6d1d665f8d03d6dfb18f51d87a510712a0f72fda44423538896b8e1c59427413' or
            descriptor['runtime_version'] != '0.160.0' or len(catalog.get('models', [])) != 1 or
            catalog['models'][0].get('slug') != 'gpt-5-6-sol-apac' or catalog['models'][0].get('upgrade') is not None):
        raise ValueError('Reviewed native catalog alias differs')
    hashes = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}
    helpers = {path: hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in HELPERS}
    binding = {'files': hashes, 'helpers': helpers}
    return files, dict(schema='isolated-codex-exec-source-v1', **binding,
        source_manifest_sha256=hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
        portfolio_data_included=False, credentials_included=False, purpose='synthetic-staging-only')


def main():
    files, record = manifest()
    attempt = FOLDER/record['source_manifest_sha256'][:12]
    context = attempt/'context'
    if context.exists():
        actual = {str(p.relative_to(context)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in context.rglob('*') if p.is_file()}
        if actual != record['files']:
            raise ValueError('Frozen context differs; do not overwrite it')
    else:
        for name, content in files.items():
            target = context/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    destination = attempt/'source-manifest.json'
    if destination.exists() and json.loads(destination.read_text()) != record:
        raise ValueError('Frozen manifest differs; do not overwrite it')
    destination.write_text(json.dumps(record, sort_keys=True, indent=2))
    print(json.dumps(record, sort_keys=True))


if __name__ == '__main__':
    main()
