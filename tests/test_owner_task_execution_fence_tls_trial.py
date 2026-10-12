"""New artifact recipe and actual restricted-session TLS-context unit checks."""
import ast
import base64
import copy
import hashlib
import lzma
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import owner_task_execution_fence_tls_candidate as candidate
import deploy_owner_task_execution_fence_trial as old
from test_owner_task_isolated_sql import runtime


class Rows:
    def __init__(self, row): self.row = row
    def fetchone(self): return self.row


class Session:
    def __init__(self, worker, row=None):
        self.w, self.role, self.sql = worker, worker.CAPABILITY, []
        self.info = SimpleNamespace(user=worker.LOGIN, dbname=worker.DATABASE, host=worker.host.HOST)
        self.autocommit = False
        self.tls = row

    def execute(self, statement):
        text = statement if isinstance(statement, str) else statement.as_string()
        self.sql.append(text)
        if text.startswith('SET LOCAL ROLE '):
            self.role = text.removeprefix('SET LOCAL ROLE ').strip('"')
            return Rows(None)
        if text.startswith('SELECT session_user'):
            return Rows(dict(login=self.info.user, role=self.role))
        if text == 'SELECT ssl,version FROM pg_stat_ssl WHERE pid=pg_backend_pid()':
            if self.role == self.w.CAPABILITY:
                return Rows(None)
            if self.role != self.w.LOGIN:
                raise AssertionError('statistics read outside registered roles')
            return Rows(self.tls)
        raise AssertionError('unregistered query')

    def transaction(self):
        raw = self
        class Transaction:
            def __enter__(self): self.role = raw.role
            def __exit__(self, typ, value, traceback):
                if typ is not None: raw.role = self.role
        return Transaction()


class TLSCandidateTests(unittest.TestCase):
    def setUp(self):
        self.worker = candidate.worker_module()
        self.controller = candidate.controller_namespace()

    def plan(self):
        paths = self.controller['PATHS']
        source = {path: (ROOT / path).read_bytes() for path in paths}
        source[self.worker.PATH] = candidate.worker_source((ROOT / candidate.BASE_WORKER).read_bytes()).encode()
        hashes = {path: hashlib.sha256(value).hexdigest() for path, value in source.items()}
        return self.controller['build'](source, hashes, runtime())

    def test_exact_frozen_predecessor_hashes(self):
        for path, sha in ((candidate.BASE_WORKER, candidate.BASE_WORKER_SHA), (candidate.BASE_CONTROL, candidate.BASE_CONTROL_SHA)):
            self.assertEqual(hashlib.sha256((ROOT / path).read_bytes()).hexdigest(), sha)

    def test_worker_recipe_rejects_source_change(self):
        with self.assertRaises(ValueError):
            candidate.worker_source((ROOT / candidate.BASE_WORKER).read_bytes() + b'\n')

    def test_controller_recipe_rejects_source_change(self):
        with self.assertRaises(ValueError):
            candidate.controller_source((ROOT / candidate.BASE_CONTROL).read_bytes() + b'\n')

    def test_recipe_requires_exactly_one_match(self):
        for text in ('missing', 'xx'):
            with self.assertRaises(ValueError): candidate.replace_once(text, 'x', 'y')

    def test_no_predecessor_global_retargeting(self):
        self.assertEqual(old.worker.JOB, 'ibkrv2stg-ownfence-20261005')
        self.assertEqual(old.worker.DATABASE, 'owner_codex_fence_20261005')
        self.assertEqual(len(old.ADDITIONS), 4)
        self.assertIsNot(self.worker, old.worker)
        self.assertNotEqual(self.worker.JOB_ID, old.JOB)
        self.assertEqual(self.worker.DATABASE, candidate.DATABASE)
        self.assertEqual(self.worker.ROLES, (candidate.OWNER, candidate.CAPABILITY, candidate.LOGIN))

    def test_exact_three_additions_preserve_811_sources(self):
        before = {str(i): 'a' * 64 for i in range(811)}
        after = dict(before, **{k: 'b' * 64 for k in self.controller['ADDITIONS']})
        self.controller['sources_valid'](before, after)
        self.assertEqual(self.controller['ADDITIONS'], {candidate.PATH, candidate.CONTROL_PATH, candidate.TEST_PATH})

    def test_unreviewed_extension_or_old_hash_rejected(self):
        before = {str(i): 'a' * 64 for i in range(811)}
        after = dict(before, **{k: 'b' * 64 for k in self.controller['ADDITIONS']})
        for change in ({'unknown': 'a' * 64}, {'0': 'c' * 64}):
            with self.assertRaises(ValueError): self.controller['sources_valid'](before, dict(after, **change))

    def test_tls_reads_actual_session_login_and_restores_capability(self):
        raw = Session(self.worker, dict(ssl=True, version='TLSv1.3'))
        self.assertEqual(self.worker.authenticated_tls(raw), dict(raw.tls, capability_statistics_row_visible=False))
        self.assertEqual(raw.role, self.worker.CAPABILITY)
        self.assertTrue(any(self.worker.LOGIN in text for text in raw.sql))
        self.assertFalse(any(self.worker.host.ADMIN in text for text in raw.sql))

    def test_missing_filtered_tls_row_fails_without_type_error(self):
        raw = Session(self.worker)
        with self.assertRaisesRegex(ValueError, 'fence_tls_invalid'):
            self.worker.authenticated_tls(raw)
        self.assertEqual(raw.role, self.worker.CAPABILITY)

    def test_false_or_unknown_protocol_fails_and_restores_role(self):
        for row in (dict(ssl=False, version='TLSv1.3'), dict(ssl=True, version='unknown')):
            raw = Session(self.worker, row)
            with self.assertRaises(ValueError): self.worker.authenticated_tls(raw)
            self.assertEqual(raw.role, self.worker.CAPABILITY)

    def test_administrator_session_rejected_before_query(self):
        raw = Session(self.worker, dict(ssl=True, version='TLSv1.3'))
        raw.info.user = self.worker.host.ADMIN
        with self.assertRaises(ValueError): self.worker.authenticated_tls(raw)
        self.assertEqual(raw.sql, [])

    def test_wrong_database_or_host_rejected_before_query(self):
        for field in ('dbname', 'host'):
            raw = Session(self.worker, dict(ssl=True, version='TLSv1.3'))
            setattr(raw.info, field, 'unregistered')
            with self.assertRaises(ValueError): self.worker.authenticated_tls(raw)
            self.assertEqual(raw.sql, [])

    def test_wrong_effective_role_or_autocommit_rejected(self):
        raw = Session(self.worker, dict(ssl=True, version='TLSv1.3'))
        raw.role = self.worker.OWNER
        with self.assertRaises(ValueError): self.worker.authenticated_tls(raw)
        raw = Session(self.worker, dict(ssl=True, version='TLSv1.3'))
        raw.autocommit = True
        with self.assertRaises(ValueError): self.worker.authenticated_tls(raw)
        self.assertEqual(raw.sql, [])

    def test_exception_rollback_restores_role(self):
        raw = Session(self.worker, dict(ssl=True, version='TLSv1.3'))
        original = raw.execute
        def execute(statement):
            if isinstance(statement, str) and statement.startswith('SELECT ssl,version') and raw.role == self.worker.LOGIN:
                raise RuntimeError('synthetic transport loss')
            return original(statement)
        raw.execute = execute
        with self.assertRaises(RuntimeError): self.worker.authenticated_tls(raw)
        self.assertEqual(raw.role, self.worker.CAPABILITY)

    def test_bounded_new_bundle_and_new_bindings(self):
        value = self.plan()
        args = value['definition']['properties']['template']['containers'][0]['args']
        self.assertLessEqual(len(self.worker.canonical(args)), 65536)
        self.assertNotIn(chr(36), self.worker.canonical(args).decode())
        packed = args[3].split("b85decode('")[1].split("'.replace")[0]
        text = lzma.decompress(base64.b85decode(packed.replace(':', chr(36)))).decode()
        compile(text, 'new-fence-bundle', 'exec')
        assignments = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(text).body
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in ('_sources', '_modules', '_hashes')}
        body = assignments['_sources'][candidate.PATH]
        self.assertIn(candidate.OPERATION, body)
        self.assertIn('ssl = authenticated_tls(raw)', body)
        self.assertNotIn("DATABASE = 'owner_codex_fence_20261005'", body)
        self.assertTrue(all(hashlib.sha256(s.encode()).hexdigest() == assignments['_hashes'][p] for p, s in assignments['_sources'].items()))

    def test_actual_generated_module_graph_imports_without_sql(self):
        value = self.plan()
        args = value['definition']['properties']['template']['containers'][0]['args']
        packed = args[3].split("b85decode('")[1].split("'.replace")[0]
        text = lzma.decompress(base64.b85decode(packed.replace(':', chr(36)))).decode()
        nodes = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(text).body
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in ('_sources', '_modules')}
        bootstrap = '''import sys,types,psycopg
def forbidden(*a,**k):raise RuntimeError("sql_on_import")
psycopg.connect=forbidden
pkg=types.ModuleType("ibkr_etl");pkg.__path__=[];sys.modules["ibkr_etl"]=pkg
'''
        bootstrap += 'sources=' + repr(nodes['_sources']) + '\nmodules=' + repr(nodes['_modules']) + '\n'
        bootstrap += '''for path,name in modules:
 m=types.ModuleType(name);m.__file__=path;m.__package__="ibkr_etl" if name.startswith("ibkr_etl.") else ""
 sys.modules[name]=m
 if name.startswith("ibkr_etl."):setattr(pkg,name.rsplit(".",1)[1],m)
 exec(compile(sources[path],path,"exec"),m.__dict__)
print("new_module_graph_imported_without_sql")
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-B', '-c', 'exec(compile(__import__("sys").stdin.read(),"new-import","exec"))'],
                input=bootstrap, cwd=directory, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'new_module_graph_imported_without_sql')

    def test_known_execution_defaults_only(self):
        expected = self.plan()['definition']['properties']['template']
        native = copy.deepcopy(expected)
        native['initContainers'] = []
        native['containers'][0]['env'][0]['secretRef'] = 'cappjob-' + candidate.JOB
        self.assertEqual(self.controller['checked_template'](native, expected), expected)

    def test_nonempty_init_or_unregistered_secret_alias_rejected(self):
        expected = self.plan()['definition']['properties']['template']
        for key in ('init', 'secret'):
            native = copy.deepcopy(expected)
            if key == 'init': native['initContainers'] = [dict(name='unknown')]
            else: native['containers'][0]['env'][0]['secretRef'] = 'cappjob-unknown'
            with self.assertRaises(ValueError): self.controller['checked_template'](native, expected)

    def test_exact_new_manual_target_and_identity(self):
        value = self.plan()['definition']
        self.assertEqual(value['tags']['operation'], candidate.OPERATION)
        self.assertEqual(value['identity']['userAssignedIdentities'], {old.MI: {}})
        self.assertEqual(value['properties']['configuration']['replicaRetryLimit'], 0)
        self.assertEqual(self.controller['JOB'], self.worker.JOB_ID)
        self.assertNotEqual(self.controller['PREFIX'], old.PREFIX)

    def test_still_no_live_audit_or_runtime_claim(self):
        source = candidate.worker_source((ROOT / candidate.BASE_WORKER).read_bytes())
        self.assertIn('protected_worker_receipts_verified=False', source)
        self.assertIn('runtime_process_cessation_verified=False', source)
        self.assertIn('website_owner_authenticated=False', source)
        self.assertIn('step_4_started=False', source)
        self.assertEqual(self.worker.DDL_SHA, old.worker.DDL_SHA)


if __name__ == '__main__':
    unittest.main()
