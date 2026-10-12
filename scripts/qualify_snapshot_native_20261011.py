"""Source-only native snapshot qualification on its own immutable base image."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import unittest

APP = Path('/app')
Q = Path('/qualification/snapshot-correction')

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def inventory():
    return {p.relative_to(APP).as_posix(): sha(p) for p in APP.rglob('*')
            if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}

def main():
    if os.getuid() != 10001: raise ValueError('Non-root qualification required')
    plan = json.loads((Q/'prepared.json').read_bytes())
    before = inventory()
    if any(before.get(n) != h for n,h in plan['baseline'].items()) or any(n in before for n in plan['new_files']):
        raise ValueError('Exact installed source baseline required')
    for name, digest in plan['candidate'].items():
        path = Q/('inherited' if name in plan['inherited_assets'] else 'payload')/name
        if sha(path) != digest: raise ValueError('Candidate source drift')
        (APP/name).parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(path, APP/name)
    after = inventory()
    changed = {n for n in set(before)|set(after) if before.get(n) != after.get(n)}
    if changed != set(plan['candidate']): raise ValueError('Unrelated application files changed')
    sys.path[:0] = [str(APP/'src'), str(APP/'scripts'), str(Q/'tests')]
    for name in plan['dependency_closure']:
        importlib.import_module('ibkr_etl.'+name)
    for name in ('src','scripts'):
        (Q/name).symlink_to(APP/name, target_is_directory=True)
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(Q/'tests')))
    if not result.wasSuccessful() or result.skipped or result.testsRun != plan['test_count']:
        raise ValueError('Exact no-skip native suite required')
    if inventory() != after: raise ValueError('Tests changed installed source')
    print('SNAPSHOT_CORRECTION_NATIVE '+json.dumps({'passed':True,'tests':result.testsRun,
        'candidate_hashes':plan['candidate'],'dependency_modules':len(plan['dependency_closure']),
        'unrelated_files_preserved':True,'database_access':False,'private_fixture_uploaded':False}), flush=True)

if __name__ == '__main__': main()
