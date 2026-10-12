"""Revocation adapter and exact new generated artifact regression checks."""
import ast
import base64
import copy
import hashlib
import lzma
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import owner_task_execution_fence_authority_candidate as candidate
import owner_task_execution_fence_tls_candidate as tls
from ibkr_etl.owner_codex_execution_fence import ExecutionFenceStore
from ibkr_etl.owner_codex_worker import WorkerError
from test_owner_codex_execution_fence import FIXTURE_SCHEMA
from test_owner_task_isolated_sql import runtime


class AuthorityTrialTests(unittest.TestCase):
    def setUp(self):
        self.worker = candidate.worker_module()
        self.control = candidate.controller_namespace()

    def row(self, enabled=True):
        return dict(tenant_id=self.worker.TENANT, worker_id=self.worker.WORKER,
            service_principal_id=self.worker.PRINCIPAL, enabled=enabled)

    def plan(self):
        source = {path: (ROOT / path).read_bytes() for path in self.control['PATHS']}
        source[candidate.PATH] = candidate.worker_source((ROOT / candidate.BASE_WORKER).read_bytes()).encode()
        return self.control['build'](source, {k: hashlib.sha256(v).hexdigest() for k, v in source.items()}, runtime())

    def test_enabled_authority_exact_binding(self):
        result = self.worker.resolve_authority(self.row())
        self.assertIs(result.enabled, True)
        self.assertEqual(result.worker_id, self.worker.WORKER)
        self.assertEqual(result.authorization_source, 'database_service')

    def test_disabled_or_missing_authority_returns_none(self):
        self.assertIsNone(self.worker.resolve_authority(None))
        for enabled in (False, None, 0, 1, 'true', 'false'):
            self.assertIsNone(self.worker.resolve_authority(self.row(enabled)))

    def test_disabled_reservation_is_access_denied_not_storage_failure(self):
        db = sqlite3.connect(':memory:'); self.addCleanup(db.close)
        db.row_factory = sqlite3.Row; db.executescript(FIXTURE_SCHEMA)
        store = ExecutionFenceStore(db, authorize_worker=lambda work: self.worker.resolve_authority(self.row(False)))
        with self.assertRaises(WorkerError) as caught:
            store.reserve(self.worker.intent(self.worker.work(1)))
        self.assertEqual(caught.exception.code, 'access_denied')
        self.assertFalse(db.in_transaction)
        self.assertEqual(db.execute('SELECT count(*) FROM owner_codex_execution_fences').fetchone()[0], 0)

    def test_actual_fresh_resolver_revocation_blocks_existing_reservation(self):
        db = sqlite3.connect(':memory:'); self.addCleanup(db.close)
        db.row_factory = sqlite3.Row; db.executescript(FIXTURE_SCHEMA)
        current = self.row(); calls = []
        def authorize(work):
            calls.append(work)
            return self.worker.resolve_authority(current)
        store = ExecutionFenceStore(db, authorize_worker=authorize)
        permit = store.reserve(self.worker.intent(self.worker.work(1)))
        current['enabled'] = False
        with self.assertRaises(WorkerError) as caught: store.verify_active(permit)
        self.assertEqual(caught.exception.code, 'access_denied')
        self.assertEqual(len(calls), 2)
        self.assertEqual(db.execute('SELECT state FROM owner_codex_execution_fences').fetchone()[0], 'reserved')

    def test_invalid_enabled_identity_remains_denied(self):
        row = self.row(); row['service_principal_id'] = 'invalid'
        with self.assertRaises(ValueError): self.worker.resolve_authority(row)

    def test_all_nine_checks_and_exact_revocation_expectation_preserved(self):
        source = candidate.worker_source((ROOT / candidate.BASE_WORKER).read_bytes())
        self.assertEqual(self.worker.CHECKS, tls.worker_module().CHECKS)
        self.assertIn("error.code == 'access_denied', 'fence_revocation_denial_invalid'", source)
        self.assertIn('return resolve_authority(row)', source)
        self.assertEqual(self.worker.DDL_SHA, tls.worker_module().DDL_SHA)

    def test_preserves_predecessor_modules_and_physical_scopes(self):
        self.assertEqual(tls.worker_module().DATABASE, tls.DATABASE)
        self.assertNotEqual(self.worker.DATABASE, tls.DATABASE)
        self.assertNotEqual(self.worker.JOB, tls.JOB)
        self.assertEqual(self.worker.DATABASE, candidate.DATABASE)
        self.assertLessEqual(len(self.worker.JOB), 32)

    def test_source_extension_exactly_three_over_814(self):
        before = {str(i): 'a' * 64 for i in range(814)}
        after = dict(before, **{k: 'b' * 64 for k in self.control['ADDITIONS']})
        self.control['sources_valid'](before, after)
        self.assertEqual(self.control['ADDITIONS'], {candidate.PATH, candidate.CONTROL_PATH, candidate.TEST_PATH})
        for change in ({'unknown': 'c' * 64}, {'0': 'd' * 64}):
            with self.assertRaises(ValueError): self.control['sources_valid'](before, dict(after, **change))

    def test_predecessor_byte_drift_rejected(self):
        with self.assertRaises(ValueError): candidate.worker_source((ROOT / candidate.BASE_WORKER).read_bytes() + b'\n')
        with self.assertRaises(ValueError): candidate.controller_source((ROOT / candidate.BASE_CONTROL).read_bytes() + b'\n')

    def unpack(self):
        value = self.plan()
        args = value['definition']['properties']['template']['containers'][0]['args']
        self.assertLessEqual(len(self.worker.canonical(args)), 65536)
        self.assertNotIn(chr(36), self.worker.canonical(args).decode())
        packed = args[3].split("b85decode('")[1].split("'.replace")[0]
        text = lzma.decompress(base64.b85decode(packed.replace(':', chr(36)))).decode()
        nodes = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(text).body
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in ('_sources', '_modules', '_hashes')}
        return nodes

    def test_actual_bundle_contains_correction_and_exact_new_target(self):
        nodes = self.unpack(); source = nodes['_sources'][candidate.PATH]
        self.assertIn(candidate.OPERATION, source)
        self.assertIn('return resolve_authority(row)', source)
        self.assertIn('ssl = authenticated_tls(raw)', source)
        self.assertIn('protected_worker_receipts_verified=False', source)
        self.assertTrue(all(hashlib.sha256(v.encode()).hexdigest() == nodes['_hashes'][k] for k,v in nodes['_sources'].items()))

    def test_actual_generated_graph_imports_without_sql(self):
        nodes = self.unpack()
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
print("authority_graph_imported_without_sql")
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-B', '-c', 'exec(compile(__import__("sys").stdin.read(),"authority-import","exec"))'],
                input=bootstrap, cwd=directory, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'authority_graph_imported_without_sql')

    def test_known_execution_defaults_still_exact(self):
        expected = self.plan()['definition']['properties']['template']
        native = copy.deepcopy(expected); native['initContainers'] = []
        native['containers'][0]['env'][0]['secretRef'] = 'cappjob-' + candidate.JOB
        self.assertEqual(self.control['checked_template'](native, expected), expected)
        native['containers'][0]['env'][0]['secretRef'] = 'cappjob-unregistered'
        with self.assertRaises(ValueError): self.control['checked_template'](native, expected)

    def test_manual_once_no_new_identity_or_production_activation(self):
        definition = self.plan()['definition']
        self.assertEqual(definition['identity']['userAssignedIdentities'], {self.control['MI']: {}})
        self.assertEqual(definition['properties']['configuration']['replicaRetryLimit'], 0)
        self.assertEqual(definition['properties']['configuration']['triggerType'], 'Manual')
        self.assertNotEqual(self.control['PREFIX'], tls.controller_namespace()['PREFIX'])
        source = candidate.worker_source((ROOT / candidate.BASE_WORKER).read_bytes())
        for flag in ('protected_worker_receipts_verified=False', 'runtime_process_cessation_verified=False',
            'website_owner_authenticated=False', 'step_4_started=False'):
            self.assertIn(flag, source)


if __name__ == '__main__': unittest.main()
