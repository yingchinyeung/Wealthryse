"""Prepare an isolated upgrade workspace. Never calls Azure or deploys anything."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261009-v1'
PREVIOUS = ROOT / '.work/platform-corrections-20261009-v1'

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    if OUT.exists():
        raise SystemExit('Upgrade workspace exists; preserve and inspect it.')
    baseline = json.loads((PREVIOUS / 'baseline-124733.json').read_bytes())
    deployed = json.loads((PREVIOUS / 'access-deployment-v1/verified.json').read_bytes())
    if baseline['web']['image'] != deployed['image'] or deployed['status'] != 'passed':
        raise ValueError('Verified website baseline differs')
    runtime = OUT / 'runtime'
    shutil.copytree(PREVIOUS / 'web-v2/local-runtime', runtime,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(PREVIOUS / 'web-v2/context/tests', OUT / 'tests',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    (OUT / 'scripts').mkdir()
    for name in ('release_internal_reports.py', 'alpha_t_presentation_runtime.mjs'):
        shutil.copyfile(runtime / 'scripts' / name, OUT / 'scripts' / name)
    (OUT / 'worker').mkdir()
    shutil.copyfile(PREVIOUS / 'changed-sources/worker/generate_month_end_package.py',
                    OUT / 'worker/generate_month_end_package.py')
    (OUT / 'evidence').mkdir()
    shutil.copyfile(PREVIOUS / 'baseline-124733.json', OUT / 'evidence/live-baseline.json')
    source_files = ['src/ibkr_etl/web.py', 'src/ibkr_etl/agent_tools.py']
    manifest = {name: sha(runtime / name) for name in source_files}
    manifest['worker/generate_month_end_package.py'] = sha(OUT / 'worker/generate_month_end_package.py')
    record = {'status': 'prepared_baseline_only', 'deployment_authorized_by_this_script': False,
              'base_images': {'web': baseline['web']['image'], **{j['name']: j['configuration']['image'] for j in baseline['jobs']}},
              'baseline_sha256': manifest, 'financial_renderers_changed': False,
              'production_writes': 0}
    (OUT / 'baseline-manifest.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print('Prepared isolated upgrade workspace from verified deployed sources.')

if __name__ == '__main__':
    main()
