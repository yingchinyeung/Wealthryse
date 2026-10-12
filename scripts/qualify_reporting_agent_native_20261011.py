"""Run inside source-only consumer builds; no database credentials or data."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

APP = Path('/app')
Q = Path('/qualification/reporting-agent')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory():
    return {p.relative_to(APP).as_posix(): sha(p) for p in APP.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}


def main():
    if os.getuid() != 10001:
        raise ValueError('Non-root build required')
    plan = json.loads((Q / 'prepared.json').read_bytes())
    before = inventory()
    if any(before.get(n) != h for n, h in plan['baseline'].items()) or any(n in before for n in plan['new_files']):
        raise ValueError('Exact consumer baseline required')
    for name, digest in plan['candidate'].items():
        path = Q / 'payload' / name
        if sha(path) != digest:
            raise ValueError('Qualified source changed')
        shutil.copyfile(path, APP / name)
    release = APP / 'ALPHA_T_RUNTIME_RELEASE.json'
    metadata = json.loads(release.read_bytes())
    metadata['installed_files_sha256'].update(plan['candidate'])
    metadata['scope'] = plan['scope']
    release.write_text(json.dumps(metadata, sort_keys=True, indent=2), encoding='utf-8')
    after = inventory()
    changed = {n for n in set(before) | set(after) if before.get(n) != after.get(n)}
    if changed != set(plan['candidate']) | {'ALPHA_T_RUNTIME_RELEASE.json'}:
        raise ValueError('Unrelated installed file changed')
    sys.path[:0] = [str(APP / 'src'), str(APP / 'scripts')]
    # Source-inspection tests resolve ../src from their own location. Point
    # them at the actual installed candidate, not a copied test-only source.
    (Q / 'src').symlink_to(APP / 'src', target_is_directory=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(Q / 'tests')))
    if not result.wasSuccessful() or result.testsRun != 33 or result.skipped:
        raise ValueError('Exact native focused suite required')
    runtime = subprocess.run([sys.executable, 'scripts/check_report_runtime.py'], cwd=APP,
                             capture_output=True, text=True, timeout=60)
    if runtime.returncode:
        raise ValueError('Installed runtime check failed')
    if inventory() != after:
        raise ValueError('Tests changed installed application files')
    print('REPORTING_AGENT_NATIVE ' + json.dumps({'passed': True, 'tests': 33,
          'consumer': plan['consumer'], 'candidate_hashes': plan['candidate'],
          'changed_files': sorted(changed), 'unrelated_files_preserved': True,
          'runtime_passed': True, 'database_access': False, 'private_fixture_uploaded': False}), flush=True)


if __name__ == '__main__':
    main()
