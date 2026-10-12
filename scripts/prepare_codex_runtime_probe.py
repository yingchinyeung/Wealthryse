"""Five-file isolated build context: no application/source data or credentials."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT/'.work/codex-runtime-20261003'


def manifest():
    paths = {
        'codex-runtime.lock.json':'deploy/codex-runtime.lock.json',
        'Dockerfile':'deploy/codex-runtime-probe.Dockerfile',
        'src/ibkr_etl/codex_runtime_probe.py':'src/ibkr_etl/codex_runtime_probe.py',
        'scripts/install_pinned_codex_runtime.py':'scripts/install_pinned_codex_runtime.py',
        'scripts/run_codex_runtime_probe.py':'scripts/run_codex_runtime_probe.py',
    }
    files = {name:(ROOT/path).read_bytes() for name,path in paths.items()}
    hashes = {name:hashlib.sha256(content).hexdigest() for name,content in sorted(files.items())}
    driver = hashlib.sha256((ROOT/'scripts/deploy_codex_runtime_probe.py').read_bytes()).hexdigest()
    return files, dict(schema='isolated-codex-probe-source-v1', files=hashes,
        build_driver_sha256=driver,
        source_manifest_sha256=hashlib.sha256(json.dumps({'files':hashes,'build_driver':driver},sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        portfolio_data_included=False, credentials_included=False, purpose='synthetic-staging-only')


def main():
    files, record = manifest()
    attempt = FOLDER/record['source_manifest_sha256'][:12]
    context = attempt/'context'
    if context.exists():
        actual = {str(p.relative_to(context)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in context.rglob('*') if p.is_file()}
        if actual != record['files']:
            raise ValueError('Existing frozen build context differs; use a separately reviewed attempt')
    else:
        for name, content in files.items():
            target = context/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(content)
    (attempt/'source-manifest.json').write_text(json.dumps(record,sort_keys=True,indent=2))
    print(json.dumps(record,sort_keys=True))


if __name__ == '__main__':
    main()
