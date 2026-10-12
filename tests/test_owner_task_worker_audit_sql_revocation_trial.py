"""Exact error-cause and source-preserving qualification, not live SQL proof."""
import ast
import base64
import hashlib
import lzma
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from psycopg import OperationalError, pq

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import owner_task_worker_audit_sql_revocation_candidate as c
from test_owner_task_isolated_sql import runtime


class RevocationTrialTests(unittest.TestCase):
    def setUp(self):
        self.w=c.worker_module();self.n=c.controller_namespace()

    def error(self,*,message=None,**changes):
        w=self.w
        con=dict(status=pq.ConnStatus.BAD,host=w.host.HOST.encode(),db=w.DATABASE.encode(),
            user=w.READER_LOGIN.encode(),error_message=(message or ('FATAL:  role "'+w.READER_LOGIN+'" is not permitted to log in\n')).encode(),
            info=[SimpleNamespace(keyword=b'sslmode',val=b'verify-full'),
                SimpleNamespace(keyword=b'sslrootcert',val=w.credential_parser.CA_PATH.encode())])
        con.update(changes)
        return OperationalError('private connection details',pgconn=SimpleNamespace(**con))

    def admin(self,login=False):
        return SimpleNamespace(execute=lambda q,p:SimpleNamespace(fetchone=lambda:dict(rolcanlogin=login)))

    def plan(self):
        source={p:(ROOT/p).read_bytes() for p in self.n['PATHS']}
        source[c.PATH]=c.worker_source((ROOT/c.BASE_WORKER).read_bytes()).encode()
        return self.n['build'](source,{p:hashlib.sha256(v).hexdigest() for p,v in source.items()},runtime())

    def test_only_three_additions_all_825_predecessors_frozen(self):
        before={f'old-{i}.py':'a'*64 for i in range(825)}
        after=dict(before,**{p:'b'*64 for p in c.ADDITIONS})
        self.n['sources_valid'](before,after)
        with self.assertRaises(ValueError):
            self.n['sources_valid'](before,dict(after,**{'old-0.py':'c'*64}))

    def test_new_physical_names_never_restart_failed_trial(self):
        self.assertNotEqual(self.w.JOB,c.previous.worker.JOB)
        self.assertNotEqual(self.w.DATABASE,c.previous.worker.DATABASE)
        self.assertEqual(len(set(self.w.ROLES)),5)
        self.assertTrue(set(self.w.ROLES).isdisjoint(c.previous.worker.ROLES))
        self.assertEqual(self.w.DDL_SHA,c.previous.worker.DDL_SHA)

    def test_missing_sqlstate_requires_exact_finished_connection_and_catalog(self):
        proof=self.w.revocation_proof(self.error(),self.admin())
        self.assertTrue(proof['exact_role_nologin_fatal'])
        self.assertIsNone(proof['driver_sqlstate'])
        self.assertNotIn('private',str(proof))

    def test_network_password_other_role_and_multiple_fatals_rejected(self):
        for text in ('connection timeout expired','password authentication failed',
            'FATAL:  role "other-login" is not permitted to log in',
            'FATAL:  role "'+self.w.READER_LOGIN+'" is not permitted to log in\nFATAL:  another cause'):
            with self.subTest(text=text),self.assertRaises(ValueError):
                self.w.revocation_proof(self.error(message=text),self.admin())

    def test_error_type_and_query_error_not_connection_denial(self):
        for error in (RuntimeError('connection timeout'),OperationalError('generic failure')):
            with self.subTest(error=type(error).__name__),self.assertRaises(ValueError):
                self.w.revocation_proof(error,self.admin())

    def test_unregistered_finished_connection_rejected(self):
        for changes in (dict(host=b'other-host'),dict(db=b'ibkr_staging'),dict(user=b'other'),
            dict(status=pq.ConnStatus.OK),dict(info=[]),dict(error_message=b'X'*4097)):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                self.w.revocation_proof(self.error(**changes),self.admin())

    def test_catalog_must_independently_confirm_login_disabled(self):
        with self.assertRaisesRegex(ValueError,'fence_audit_revocation_catalog_invalid'):
            self.w.revocation_proof(self.error(),self.admin(login=True))

    def test_failed_sources_and_evidence_remain_pinned(self):
        source=c.controller_source((ROOT/c.BASE_CONTROL).read_bytes())
        self.assertIn("failed_sql_control['checked_job']",source)
        self.assertIn("failed_sql_pf['predecessor_sources']",source)
        self.assertIn("list(failed_sql_control['worker'].CHECKS[:6])",source)
        self.assertIn("proof['exact_role_nologin_fatal'] is True",source)
        self.assertLess(source.index('identity = p.old.prior.parent.live_identity'),
            source.index("failed_sql_control['checked_job']"))

    def test_distinct_failure_pins_do_not_retarget_older_guards(self):
        for name in ('FAILURE_SHA','FAILURE_READBACK_SHA','SOURCE_SHA','SOURCE_READBACK_SHA'):
            self.assertEqual(getattr(c,name),getattr(c.previous,name))
        self.assertNotEqual(c.SQL_FAILURE_SHA,c.FAILURE_SHA)
        source=c.controller_source((ROOT/c.BASE_CONTROL).read_bytes())
        self.assertIn('==candidate.SQL_FAILURE_SHA',source)
        self.assertIn('==candidate.SQL_FAILURE_READBACK_SHA',source)
        self.assertIn('== candidate.FAILURE_SHA',source)

    def test_launch_still_single_pinned_private_scope(self):
        plan=self.plan();config=plan['definition']['properties']['configuration']
        self.assertEqual(config['replicaRetryLimit'],0)
        self.assertEqual(config['manualTriggerConfig'],dict(parallelism=1,replicaCompletionCount=1))
        self.assertEqual(config['identitySettings'],[dict(identity=self.n['MI'],lifecycle='None')])
        self.assertEqual(config['secrets'][0]['keyVaultUrl'],self.n['image'].SECRET_VERSION_URL)
        self.assertEqual(plan['definition']['properties']['template']['containers'][0]['image'],self.n['image'].IMAGE)

    def test_actual_derived_worker_bundle_imports_without_sql(self):
        arg=self.plan()['definition']['properties']['template']['containers'][0]['args'][3]
        node=next(n for n in ast.walk(ast.parse(arg)) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='b85decode')
        script=lzma.decompress(base64.b85decode(ast.literal_eval(node.args[0].func.value).replace(':',chr(36)))).decode()
        values={n.targets[0].id:ast.literal_eval(n.value) for n in ast.parse(script).body
            if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id in ('_sources','_modules')}
        code="import sys,types\np=types.ModuleType('ibkr_etl');p.__path__=[];sys.modules['ibkr_etl']=p\n"
        code+='_sources='+repr(values['_sources'])+'\n_modules='+repr(values['_modules'])+'\n'
        code+="for path,name in _modules:\n m=types.ModuleType(name);m.__file__=path;m.__package__=name.rpartition('.')[0];sys.modules[name]=m;exec(compile(_sources[path],path,'exec'),m.__dict__)\nprint('no_sql_imported')"
        result=subprocess.run([sys.executable,'-I','-'],input=code,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'no_sql_imported')


if __name__=='__main__':
    unittest.main()
