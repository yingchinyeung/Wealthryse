"""Execute the actual packaged imports with credentials and all I/O disabled."""
import base64
import gzip
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import deploy_owner_task_postgres_trial as deploy


class BundleImportTests(unittest.TestCase):
    def test_actual_run_and_cleanup_payload_imports_without_io(self):
        for stage in ('run','cleanup'):
            with self.subTest(stage=stage):
                args,record = deploy.bundle(stage)
                encoded = args[1].split("base64.b64decode('",1)[1].split("'",1)[0]
                payload = gzip.decompress(base64.b64decode(encoded)).decode()
                # Fresh interpreter avoids contaminating existing ibkr_etl modules.
                # Non-main globals prevent the trusted administrator wrapper run.
                child = """import json,os,socket,sys,psycopg
from unittest.mock import patch
calls=[]
def forbidden(*args,**kwargs):
    calls.append('io')
    raise AssertionError('Database/network access forbidden during packaged import')
os.environ.pop('DATABASE_ADMIN_URL',None)
os.environ.pop('OWNER_TASK_TRIAL_STAGE',None)
scope={'__name__':'owner_task_trial_import_acceptance'}
with patch.object(psycopg,'connect',forbidden),patch.object(socket,'create_connection',forbidden),patch.object(socket.socket,'connect',forbidden),patch.object(socket.socket,'connect_ex',forbidden):
    exec(compile(sys.stdin.read(),'actual-reviewed-payload','exec'),scope)
assert calls==[]
assert callable(scope['verify_trial']) and callable(scope['main'])
for name in ('util','db','owner_codex_tasks'):
    module=sys.modules['ibkr_etl.'+name]
    assert module.__file__=='/app/src/ibkr_etl/'+name+'.py'
    assert module.__package__=='ibkr_etl'
assert scope['h'].__file__=='/app/scripts/verify_owner_task_postgres.py'
assert len(scope['h'].MIGRATION_SHA256)==64
"""
                environment = dict(os.environ)
                environment.pop('DATABASE_ADMIN_URL',None)
                environment.pop('OWNER_TASK_TRIAL_STAGE',None)
                environment['PYTHONPATH'] = str(ROOT/'src')
                result = subprocess.run([sys.executable,'-c',child],input=payload,text=True,
                    capture_output=True,env=environment,cwd=ROOT,timeout=25)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(result.stdout,'')


if __name__ == '__main__':
    unittest.main()
