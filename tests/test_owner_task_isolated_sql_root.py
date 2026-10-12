"""New physical continuation only; no live SQL or cloud acceptance from mocks."""
import base64
import copy
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import deploy_owner_task_isolated_sql_root as control
import owner_task_isolated_sql_root_launch as launch
import run_owner_task_isolated_sql_root as worker
import test_owner_task_isolated_sql as fixture
from test_owner_task_isolated_sql_image_root import TrackedEnvironment


def plan():
    source={p:(ROOT/p).read_bytes() for p in launch.SOURCE_PATHS}
    return launch.build(source,{p:hashlib.sha256(b).hexdigest() for p,b in source.items()},fixture.runtime())


class RootContinuationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.plan=plan()

    def synthetic_launcher(self,values,*,runtime_matches=True,hash_valid=True):
        env=TrackedEnvironment(values);calls=[]
        runtime=types.ModuleType('owner_task_provider_runtime');runtime.check_runtime=lambda:dict(qualified=runtime_matches)
        module=types.ModuleType('run_owner_task_isolated_sql_root')
        module.main=lambda secret,migration,connect:calls.append((secret,migration)) or dict(status='unaccepted')
        pq=types.ModuleType('psycopg');pq.connect=lambda **kw:self.fail('No SQL in offline fixture')
        ns=dict(_sources={'fixture.sql':'synthetic'},_hashes={'fixture.sql':hashlib.sha256(b'synthetic').hexdigest() if hash_valid else 'a'*64},
            _modules=(),_runtime=dict(qualified=True),_migration='fixture.sql')
        output=io.StringIO()
        with patch.object(os,'environ',env),patch.dict(sys.modules,{'owner_task_provider_runtime':runtime,
                'run_owner_task_isolated_sql_root':module,'psycopg':pq}),redirect_stdout(output):
            with self.assertRaises(SystemExit) as caught:exec(launch.LAUNCHER,ns)
        self.assertEqual(caught.exception.code,1)
        return env.secret_reads,calls,output.getvalue()

    def test_new_valid_name_operation_namespace_no_old_attempt_retarget(self):
        self.assertEqual(len(worker.JOB),30)
        self.assertNotEqual(worker.OPERATION,control.predecessor.worker.OPERATION)
        self.assertNotEqual(control.JOB,control.predecessor.JOB)
        self.assertNotEqual(control.FOLDER,control.predecessor.FOLDER)
        self.assertNotEqual(control.PREFIX,control.predecessor.PREFIX)
        self.assertEqual(worker.IDENTITY_ID,control.predecessor.worker.IDENTITY_ID)
        self.assertEqual(control.STAGES,('job','start'))

    def test_worker_behavior_preserved_except_new_physical_metadata(self):
        before=(ROOT/'scripts/run_owner_task_isolated_sql_named.py').read_text()
        expected=before.replace(control.predecessor.worker.OPERATION,worker.OPERATION).replace(
            control.predecessor.worker.JOB,worker.JOB).replace(control.predecessor.worker.PREFIX,worker.PREFIX).replace(
            'Separately named synthetic SQL worker after a rejected 33-character job name.',
            'Synthetic SQL continuation after verified image-root launch rejection.')
        self.assertEqual((ROOT/'scripts/run_owner_task_isolated_sql_root.py').read_text(),expected)

    def test_exact_770_plus_four_additions(self):
        before={f'old/{i}.py':'a'*64 for i in range(770)}
        after=dict(before,**{p:'b'*64 for p in control.ADDITIONS})
        self.assertEqual(len(control.ADDITIONS),4);control.validate_sources(before,after)
        after['old/0.py']='c'*64
        with self.assertRaises(Exception):control.validate_sources(before,after)

    def test_exact_14_bundle_bounded_and_fixed_image_secret_runtime_identity_retry(self):
        p=self.plan;d=p['definition'];config=d['properties']['configuration'];container=d['properties']['template']['containers'][0]
        self.assertEqual(len(p['source_hashes']),14)
        self.assertIn('scripts/run_owner_task_isolated_sql_root.py',p['source_hashes'])
        self.assertNotIn('scripts/run_owner_task_isolated_sql_named.py',p['source_hashes'])
        self.assertEqual(container['image'],launch.prior.IMAGE)
        self.assertEqual(config['secrets'],[dict(name=worker.target.SECRET_NAME,keyVaultUrl=launch.prior.SECRET_VERSION_URL,identity=worker.IDENTITY_ID)])
        self.assertEqual(container['env'],[dict(name='OWNERTRIAL_ADMIN_URL',secretRef=worker.target.SECRET_NAME)])
        self.assertEqual(config['identitySettings'],[dict(identity=worker.IDENTITY_ID,lifecycle='None')])
        self.assertEqual(config['replicaRetryLimit'],0);self.assertEqual(config['triggerType'],'Manual')
        self.assertLessEqual(len(launch.canonical(container['args'])),65536)
        self.assertFalse(p['step_2_complete'])

    def test_guard_and_runtime_precede_credential_and_sql(self):
        text=launch.LAUNCHER
        self.assertLess(text.index('IBKR_PROJECT_ROOT'),text.index('for _path,_name'))
        self.assertLess(text.index('check_runtime()'),text.index('os.environ.pop'))
        self.assertLess(text.index('os.environ.pop'),text.index('_result=sys.modules'))
        self.assertNotIn('psycopg.connect(',text)

    def test_bad_environment_has_fixed_safe_stage_and_no_secret_read(self):
        for root in ('/tmp','/app/','',None):
            with self.subTest(root=root):
                reads,calls,text=self.synthetic_launcher(dict(IBKR_PROJECT_ROOT=root,OWNERTRIAL_ADMIN_URL='synthetic-sensitive'))
                self.assertEqual((reads,calls),(0,[]));self.assertNotIn('synthetic-sensitive',text)
                value=json.loads(text.split(' ',1)[1]);self.assertEqual(value,dict(stage='environment',sql_worker_entered=False,automatic_retry=False))

    def test_valid_image_root_runtime_failure_blocks_credential(self):
        reads,calls,text=self.synthetic_launcher(dict(IBKR_PROJECT_ROOT='/app',OWNERTRIAL_ADMIN_URL='synthetic-sensitive'),runtime_matches=False)
        self.assertEqual((reads,calls),(0,[]));self.assertEqual(json.loads(text.split(' ',1)[1])['stage'],'runtime_validation')

    def test_source_mismatch_blocks_secret_before_import(self):
        reads,calls,text=self.synthetic_launcher(dict(IBKR_PROJECT_ROOT='/app',OWNERTRIAL_ADMIN_URL='synthetic-sensitive'),hash_valid=False)
        self.assertEqual((reads,calls),(0,[]));self.assertEqual(json.loads(text.split(' ',1)[1])['stage'],'source_validation')

    def test_missing_secret_has_fixed_credential_stage(self):
        reads,calls,text=self.synthetic_launcher(dict(IBKR_PROJECT_ROOT='/app'))
        self.assertEqual((reads,calls),(1,[]));self.assertEqual(json.loads(text.split(' ',1)[1])['stage'],'credential_acquisition')

    def test_qualified_fixture_reaches_one_pop_and_fixed_worker_only(self):
        reads,calls,text=self.synthetic_launcher(dict(IBKR_PROJECT_ROOT='/app',OWNERTRIAL_ADMIN_URL='synthetic-only'))
        self.assertEqual((reads,calls),(1,[('synthetic-only','synthetic')]))
        self.assertEqual(text,'')

    def test_real_serialized_bundle_imports_before_windows_runtime_rejection(self):
        if sys.platform!='win32':self.skipTest('Windows-specific local runtime rejection fixture')
        args=self.plan['definition']['properties']['template']['containers'][0]['args']
        packed=args[3].split("base64.b64decode('",1)[1].split("')",1)[0]
        source=lzma.decompress(base64.b64decode(packed)).decode()
        env=TrackedEnvironment({'IBKR_PROJECT_ROOT':'/app','OWNERTRIAL_ADMIN_URL':'synthetic-sensitive'})
        output=io.StringIO()
        with patch.object(os,'environ',env),patch.dict(sys.modules),redirect_stdout(output):
            with self.assertRaises(SystemExit):exec(source,{})
        self.assertEqual(env.secret_reads,0)
        self.assertEqual(json.loads(output.getvalue().split(' ',1)[1])['stage'],'runtime_validation')

    def test_null_empty_descriptors_allowed_but_arrays_env_or_resources_not(self):
        identity=dict(principalId='11111111-1111-4111-8111-111111111111',clientId='22222222-2222-4222-8222-222222222222')
        v=copy.deepcopy(self.plan['definition']);v.update(id=control.JOB,name=worker.JOB,type='Microsoft.App/jobs')
        v['identity']['userAssignedIdentities'][control.MI]=identity;v['properties']['provisioningState']='Succeeded'
        template=v['properties']['template'];template.update(initContainers=None,volumes=None)
        template['containers'][0]['resources']['ephemeralStorage']=''
        before=copy.deepcopy(v);control.validate_job(v,self.plan,identity);self.assertEqual(v,before)
        for mode in ('array','cpu','env'):
            bad=copy.deepcopy(v)
            if mode=='array':bad['properties']['template']['volumes']=[]
            if mode=='cpu':bad['properties']['template']['containers'][0]['resources']['cpu']=1
            if mode=='env':bad['properties']['template']['containers'][0]['env'].append(dict(name='PGPASSWORD',value='synthetic'))
            with self.subTest(mode=mode),self.assertRaises(Exception):control.validate_job(bad,self.plan,identity)

    def test_old_job_mutation_unknown_route_and_grants_rejected_before_credentials(self):
        client=control.Transport();client.credential=Mock()
        for method,path,stage in [('PUT',control.predecessor.JOB,'job'),('POST',control.predecessor.JOB+'/start','start'),('DELETE',control.JOB,'job'),('GET',control.JOB+'/executions/unknown','job')]:
            with self.subTest(path=path),self.assertRaises(Exception):client.call(method,path,'2025-07-01',stage=stage,plan=self.plan,body={})
        client.credential.get_token.assert_not_called()
        for stage in ('identity','acr','secret'):
            with self.assertRaises(Exception):control.effect(stage,self.plan)

    def test_consumed_stage_blocks_before_load_or_credentials(self):
        with patch.object(Path,'exists',return_value=True),patch.object(control,'load') as load:
            with self.assertRaises(Exception):control.dispatch(Mock(),'start')
            load.assert_not_called()

    def test_fresh_names_and_catalog_checks_precede_first_installer_effect(self):
        import inspect
        text=inspect.getsource(worker.preparation._prepare_trial)
        self.assertLess(text.index('before =='),text.index('customer_database_revokes'))
        self.assertLess(text.index('assert_public_database_boundary'),text.index('customer_database_revokes'))


if __name__=='__main__':unittest.main()
