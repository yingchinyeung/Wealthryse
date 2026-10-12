"""Offline launch/scope checks, never represented as live SQL acceptance."""
import ast
import base64
import hashlib
import json
import lzma
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import owner_task_worker_audit_sql_candidate as candidate
import run_owner_task_worker_audit_sql_trial as w
from test_owner_task_isolated_sql import runtime


class AuditSQLTrialTests(unittest.TestCase):
    def setUp(self):
        self.control=candidate.controller_namespace()

    def source(self):
        return {p:(ROOT/p).read_bytes() for p in self.control['PATHS']}

    def plan(self):
        data=self.source()
        return self.control['build'](data,{p:hashlib.sha256(v).hexdigest() for p,v in data.items()},runtime())

    def decoded(self):
        args=self.plan()['definition']['properties']['template']['containers'][0]['args']
        # Extract the literal argument of base85 decoding, not execute launch.
        tree=ast.parse(args[3])
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='b85decode']
        text=ast.literal_eval(calls[0].args[0].func.value).replace(':',chr(36))
        return lzma.decompress(base64.b85decode(text)).decode()

    def test_new_physical_namespace_not_any_predecessor(self):
        self.assertEqual(w.JOB,'ibkrv2stg-ownaudsql-20261005')
        self.assertNotEqual(w.JOB,candidate.previous.JOB)
        self.assertNotEqual(w.DATABASE,candidate.previous.DATABASE)
        self.assertEqual(len(w.ROLES),5)
        self.assertEqual(len(set(w.ROLES)),5)
        self.assertNotEqual(w.LOGIN,w.READER_LOGIN)
        self.assertNotEqual(w.CAPABILITY,w.READER_CAP)

    def test_exact_821_source_extension(self):
        before={f'old-{i}.py':'a'*64 for i in range(821)}
        after=dict(before,**{p:'b'*64 for p in candidate.ADDITIONS})
        self.control['sources_valid'](before,after)
        for changes in ({'extra.py':'c'*64},{'old-0.py':'d'*64}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.control['sources_valid'](before,dict(after,**changes))

    def test_predecessor_job_validation_follows_identity_verification(self):
        source=candidate.controller_source((ROOT/candidate.BASE_CONTROL).read_bytes())
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='guards')
        body=ast.get_source_segment(source,node)
        self.assertLess(body.index("identity = p.old.prior.parent.live_identity"),
            body.index("accepted_fence_control['checked_job']"))
        self.assertLess(body.index("'fence_sql_identity_drift'"),
            body.index("accepted_fence_control['checked_job']"))

    def test_launch_is_manual_once_same_private_identity_and_digest(self):
        plan=self.plan();definition=plan['definition'];p=definition['properties']
        self.assertEqual(p['configuration']['triggerType'],'Manual')
        self.assertEqual(p['configuration']['replicaRetryLimit'],0)
        self.assertEqual(p['configuration']['manualTriggerConfig'],dict(parallelism=1,replicaCompletionCount=1))
        self.assertEqual(p['configuration']['identitySettings'],[dict(identity=self.control['MI'],lifecycle='None')])
        self.assertEqual(p['configuration']['secrets'][0]['keyVaultUrl'],self.control['image'].SECRET_VERSION_URL)
        self.assertEqual(p['template']['containers'][0]['image'],self.control['image'].IMAGE)
        self.assertEqual(definition['tags']['operation'],w.OPERATION)

    def test_bundle_contains_exact_audit_schema_and_worker(self):
        script=self.decoded();tree=ast.parse(script)
        values={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body
            if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)
            and n.targets[0].id in ('_sources','_hashes','_modules')}
        self.assertEqual(hashlib.sha256(values['_sources'][w.DDL_PATH].encode()).hexdigest(),w.DDL_SHA)
        self.assertIn((w.PATH,'run_owner_task_execution_fence_trial'),values['_modules'])
        self.assertIn(('src/ibkr_etl/owner_codex_worker_audit.py','ibkr_etl.owner_codex_worker_audit'),values['_modules'])
        self.assertEqual(values['_sources'][w.PATH],candidate.worker_source((ROOT/w.PATH).read_bytes()))
        self.assertIn('sys.modules["run_owner_task_execution_fence_trial"].main',script)
        self.assertNotIn('sys.modules["run_owner_task_isolated_sql_root"].main',script)

    def test_drifted_schema_rejected(self):
        source=self.source();source[w.DDL_PATH]+=b'\n-- unreviewed'
        with self.assertRaisesRegex(ValueError,'fence_schema_source_invalid'):
            self.control['build'](source,{p:hashlib.sha256(v).hexdigest() for p,v in source.items()},runtime())

    def test_failed_input_emits_no_secrets_no_acceptance(self):
        records=[];ddl=(ROOT/w.DDL_PATH).read_text()
        secret='not-a-secret-private-marker'
        result=w.main(secret,ddl,ddl,connect=lambda **kw:self.fail('SQL must not run'),emit=lambda value,**kw:records.append(value))
        self.assertEqual(result['status'],'unaccepted')
        self.assertEqual(result['checks'],[])
        self.assertFalse(result['step_3_complete'])
        self.assertFalse(result['step_4_started'])
        self.assertNotIn(secret,records[0])
        self.assertTrue(records[0].startswith(w.PREFIX))

    def test_payload_is_source_free_synthetic_registration(self):
        item,data=w.payload(1);result=json.loads(data)
        self.assertEqual(result['worker_evidence'],item.payload())
        self.assertNotIn(b'SYNTH_AUDIT_SQL',data)
        self.assertNotIn(item.work.lease_token.encode(),data)
        self.assertFalse(result['worker_evidence']['source_mutation_authority'])
        self.assertFalse(result['worker_evidence']['tools_authorized'])

    def test_real_connection_requires_exact_writer_reader_host_tls(self):
        parameters=dict(user=w.LOGIN,password='synthetic-only-password')
        calls=[]
        def connect(**kw):
            calls.append(kw)
            return SimpleNamespace(info=SimpleNamespace(host=w.host.HOST,dbname=w.DATABASE,
                user=kw['user'],get_parameters=lambda:dict(sslmode='verify-full',sslrootcert=w.credential_parser.CA_PATH)),
                pgconn=SimpleNamespace(ssl_in_use=True))
        w.connect_exact(connect,parameters,w.DATABASE,reader=False)
        w.connect_exact(connect,dict(parameters,user=w.READER_LOGIN),w.DATABASE,reader=True)
        self.assertTrue(all(c['autocommit'] for c in calls))
        self.assertTrue(all(c['dbname']==w.DATABASE for c in calls))
        self.assertNotIn('role=',calls[0]['options'])

    def test_wrong_login_or_database_rejected_before_connect(self):
        for user,db,reader in ((w.LOGIN,w.DATABASE,True),(w.READER_LOGIN,w.DATABASE,False),
            (w.host.ADMIN,'ibkr_staging',None)):
            with self.subTest(user=user,db=db):
                with self.assertRaises(ValueError):
                    w.connect_exact(lambda **kw:self.fail('no connection'),dict(user=user),db,reader=reader)

    def test_wrong_hostname_or_tls_rejected(self):
        for changes in (dict(host='wrong-host'),dict(ssl=False),dict(mode='require')):
            with self.subTest(changes=changes):
                closed=[]
                raw=SimpleNamespace(info=SimpleNamespace(host=changes.get('host',w.host.HOST),
                    dbname=w.DATABASE,user=w.LOGIN,get_parameters=lambda:dict(sslmode=changes.get('mode','verify-full'),sslrootcert=w.credential_parser.CA_PATH)),
                    pgconn=SimpleNamespace(ssl_in_use=changes.get('ssl',True)),close=lambda:closed.append(True))
                with self.assertRaisesRegex(ValueError,'fence_audit_transport_invalid'):
                    w.connect_exact(lambda **kw:raw,dict(user=w.LOGIN),w.DATABASE,reader=False)
                self.assertEqual(closed,[True])

    def test_all_logins_restore_idle_connection_after_denial(self):
        calls=[]
        class C:
            info=SimpleNamespace(transaction_status=0)
            def transaction(self):
                class T:
                    def __enter__(self):return self
                    def __exit__(self,*args):return False
                return T()
            def execute(self,s,p):
                calls.append(s)
                if not s.startswith('SELECT set_config'):
                    error=RuntimeError('private provider details');error.sqlstate='42501';raise error
        w.denied(C(),'DELETE FROM forbidden')
        self.assertEqual(len(calls),3)

    def test_wrong_cause_is_not_a_privilege_denial(self):
        class C:
            info=SimpleNamespace(transaction_status=0)
            def transaction(self):
                class T:
                    def __enter__(self):return self
                    def __exit__(self,*args):return False
                return T()
            def execute(self,s,p):
                raise RuntimeError('network timeout')
        with self.assertRaisesRegex(ValueError,'fence_audit_denial_wrong_cause'):
            w.denied(C(),'DELETE FROM forbidden')

    def test_transport_cannot_write_another_job_or_restart(self):
        client=self.control['Transport']();plan=self.plan()
        with self.assertRaisesRegex(ValueError,'fence_route_invalid'):
            client.call('POST',candidate.previous.worker_module().JOB_ID+'/start',body={},stage='start',plan=plan)
        with self.assertRaises(ValueError):
            client.call('DELETE',self.control['JOB'],body={},stage='job',plan=plan)

    def test_sql_script_has_identity_bound_rls_and_no_production_tools(self):
        source=(ROOT/w.PATH).read_text()
        self.assertIn('AS RESTRICTIVE',source)
        self.assertIn('session_user::text IN',source)
        self.assertIn('CURRENT_USER WITH ADMIN FALSE, INHERIT FALSE, SET TRUE',source)
        self.assertNotIn('ALTER SYSTEM',source)
        self.assertNotIn('requests.',source)
        self.assertNotIn('get_token',source)

    def test_bundle_import_graph_without_sql(self):
        tree=ast.parse(self.decoded())
        values={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body
            if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)
            and n.targets[0].id in ('_sources','_modules')}
        code="import sys,types,json\np=types.ModuleType('ibkr_etl');p.__path__=[];sys.modules['ibkr_etl']=p\n"
        code+='_sources='+repr(values['_sources'])+'\n_modules='+repr(values['_modules'])+'\n'
        code+="for path,name in _modules:\n m=types.ModuleType(name);m.__file__=path;m.__package__=name.rpartition('.')[0];sys.modules[name]=m;exec(compile(_sources[path],path,'exec'),m.__dict__)\nprint('graph_imported_no_sql')"
        result=subprocess.run([sys.executable,'-I','-'],input=code,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'graph_imported_no_sql')


if __name__=='__main__':
    unittest.main()
