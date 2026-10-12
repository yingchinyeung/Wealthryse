"""Freeze and test an isolated application candidate; no Azure changes."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.work/platform-upgrade-20261009-v1'
BASE = ROOT / '.work/platform-corrections-20261009-v1/web-v2/local-runtime'
ALLOWED = {'src/ibkr_etl/web.py', 'src/ibkr_etl/agent_tools.py',
           'src/ibkr_etl/platform_readiness.py', 'src/ibkr_etl/qualified_agent_risk.py'}

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def manifest(root):
    return {p.relative_to(root).as_posix():sha(p) for p in root.rglob('*')
            if p.is_file() and p.suffix != '.pyc' and '__pycache__' not in p.parts}

def main():
    before, after = manifest(BASE), manifest(OUT / 'runtime')
    changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    if changed != ALLOWED:
        raise ValueError('Unexpected candidate source changes: ' + repr(changed))
    env = {**os.environ, 'PORTAL_TEST_RUNTIME':str(OUT / 'runtime'),
           'PYTHONPATH':os.pathsep.join(map(str,(OUT/'runtime/src',OUT/'runtime/scripts'))),
           'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1',
           'TMP':str(ROOT/'.work/t-test-temp'),'TEMP':str(ROOT/'.work/t-test-temp')}
    run = subprocess.run([sys.executable,'-m','unittest','discover','-s',str(OUT/'tests'),'-v'],
                         env=env, cwd=OUT/'runtime',capture_output=True,text=True,encoding='utf-8',timeout=300)
    log = OUT/'evidence/local-tests.log'
    log.write_text(run.stdout+run.stderr,encoding='utf-8')
    if run.returncode:
        print(run.stderr[-6000:])
        raise SystemExit('Upgrade qualification failed; preserved log for inspection.')
    counts = re.findall(r'Ran (\d+) tests',run.stderr)
    record = {'status':'local_tests_passed','tests_run':int(counts[-1]),
              'log_sha256':sha(log),'changed_files':sorted(changed),
              'runtime_sha256':after, 'worker_sha256':sha(OUT/'worker/generate_month_end_package.py'),
              'tests_sha256':manifest(OUT/'tests'),
              'financial_renderers_and_templates_unchanged':True,
              'production_deployable':False,
              'pending':['Linux build and security scan','isolated PostgreSQL and Azure acceptance',
                         'MS extended-risk qualification','physical crisis and downstream refresh',
                         'network cost approval','disaster-recovery choices','fresh viewer/download verification']}
    (OUT/'evidence/local-qualification.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(run.stderr[-250:])
    print('Frozen local candidate only; not authorized for promotion by these tests alone.')

if __name__=='__main__':main()
