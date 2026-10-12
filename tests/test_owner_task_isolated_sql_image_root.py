import hashlib
import io
import os
from pathlib import Path
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import owner_task_isolated_sql_image_root as candidate


class TrackedEnvironment(dict):
    def __init__(self, values):
        super().__init__(values); self.secret_reads = 0

    def pop(self, key, default=None):
        if key == 'OWNERTRIAL_ADMIN_URL': self.secret_reads += 1
        return super().pop(key, default)


class ImageRootCandidateTests(unittest.TestCase):
    def exercise(self, environment, *, frozen=False, runtime_matches=True):
        env = TrackedEnvironment(environment); imported=[]; calls=[]
        source = 'mark_import()'
        runtime = types.ModuleType('owner_task_provider_runtime')
        runtime.check_runtime = lambda: {'qualified': runtime_matches}
        worker = types.ModuleType('run_owner_task_isolated_sql_named')
        worker.main = lambda secret, migration, connect: calls.append((secret, migration)) or {'status':'unaccepted'}
        psycopg = types.ModuleType('psycopg'); psycopg.connect = lambda **kw: self.fail('SQL forbidden in offline fixture')
        namespace = dict(_sources={'fixture.py':source},
            _hashes={'fixture.py':hashlib.sha256(source.encode()).hexdigest()},
            _modules=(('fixture.py','isolated_root_fixture'),),
            _runtime={'qualified':True},_migration='fixture.py')
        # The synthetic module can record import only. No real credential, DB,
        # provider runtime, cloud identity or SQL connector is used.
        source = 'import builtins\nbuiltins._isolated_root_mark()'
        namespace['_sources']['fixture.py']=source
        namespace['_hashes']['fixture.py']=hashlib.sha256(source.encode()).hexdigest()
        output=io.StringIO()
        with patch.object(os,'environ',env), patch.dict(sys.modules,{
                'owner_task_provider_runtime':runtime,'run_owner_task_isolated_sql_named':worker,'psycopg':psycopg}), \
                patch('builtins._isolated_root_mark',lambda:imported.append(True),create=True), redirect_stdout(output):
            try:
                exec(candidate.prior.LAUNCHER if frozen else candidate.candidate_launcher(),namespace)
            except SystemExit as error:
                self.assertEqual(error.code,1)
        sys.modules.pop('isolated_root_fixture',None)
        return imported,env.secret_reads,calls,output.getvalue()

    def test_frozen_launcher_rejects_docker_image_default_before_import_and_secret(self):
        result=self.exercise({'IBKR_PROJECT_ROOT':'/app','OWNERTRIAL_ADMIN_URL':'synthetic-only'},frozen=True)
        self.assertEqual(result[:3],([],0,[]))
        self.assertEqual(result[3],'OWNER_TASK_ISOLATED_SQL_LAUNCH_FAILED_V1\n')

    def test_exact_default_permits_import_but_runtime_mismatch_blocks_secret(self):
        result=self.exercise({'IBKR_PROJECT_ROOT':'/app','OWNERTRIAL_ADMIN_URL':'synthetic-only'},runtime_matches=False)
        self.assertEqual(result[:3],([True],0,[]))

    def test_qualified_synthetic_path_reaches_exact_one_pop_and_fixed_worker(self):
        result=self.exercise({'IBKR_PROJECT_ROOT':'/app','OWNERTRIAL_ADMIN_URL':'synthetic-only'})
        self.assertEqual(result[0],[True]);self.assertEqual(result[1],1)
        self.assertEqual(result[2],[('synthetic-only','import builtins\nbuiltins._isolated_root_mark()')])

    def test_other_roots_missing_aliases_and_libpq_tls_overrides_block_before_import(self):
        cases=[
            {},{'IBKR_PROJECT_ROOT':None},{'IBKR_PROJECT_ROOT':''},
            {'IBKR_PROJECT_ROOT':'/app/'},{'IBKR_PROJECT_ROOT':'/tmp'},
            {'IBKR_PROJECT_ROOT':'/app','ibkr_project_root':'/app'}]
        cases += [dict(IBKR_PROJECT_ROOT='/app',**{key:'synthetic-only'}) for key in
            ('PGHOST','PGPASSWORD','pgOPTIONS','SSL_CERT_FILE','SSL_CERT_DIR','OPENSSL_CONF','OPENSSL_MODULES')]
        for values in cases:
            with self.subTest(keys=list(values)):
                values=dict(values,OWNERTRIAL_ADMIN_URL='synthetic-only')
                self.assertEqual(self.exercise(values)[:3],([],0,[]))

    def test_invalid_environment_key_blocks_before_secret(self):
        for key in ('bad\nkey','é', 'x'*513):
            with self.subTest(key_kind=len(key)):
                self.assertEqual(self.exercise({'IBKR_PROJECT_ROOT':'/app',key:'unused'})[:3],([],0,[]))

    def test_only_single_guard_changed_and_predecessor_untouched(self):
        frozen=candidate.prior.LAUNCHER
        self.assertEqual(candidate.candidate_launcher().replace(candidate.NEW_GUARD,candidate.OLD_GUARD,1),frozen)
        self.assertEqual(candidate.prior.LAUNCHER,frozen)
        descriptor=candidate.descriptor()
        self.assertFalse(descriptor['existing_job_changed']); self.assertFalse(descriptor['sql_dispatched'])
        self.assertFalse(descriptor['step_2_complete'])

    def test_predecessor_guard_drift_rejected(self):
        with patch.object(candidate.prior,'LAUNCHER','print("not the frozen launcher")'):
            with self.assertRaisesRegex(ValueError,'isolated_sql_predecessor_guard_changed'):
                candidate.candidate_launcher()


if __name__ == '__main__': unittest.main()
