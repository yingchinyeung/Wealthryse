"""Offline Step 3 trial controls; never claims live SQL/audit/runtime acceptance."""
import ast
import base64
import copy
import hashlib
import json
import lzma
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import deploy_owner_task_execution_fence_trial as c
import run_owner_task_execution_fence_trial as w
from test_owner_task_isolated_sql import runtime


def plan():
    source = {path: (ROOT / path).read_bytes() for path in c.PATHS}
    return c.build(source, {path: hashlib.sha256(value).hexdigest() for path, value in source.items()}, runtime())


def unpack(value):
    arg = value['definition']['properties']['template']['containers'][0]['args'][3]
    packed = arg.split("b85decode('")[1].split("'.replace")[0]
    return lzma.decompress(base64.b85decode(packed.replace(':', chr(36)))).decode()


class FenceTrialTests(unittest.TestCase):
    def test_exact_four_additions_preserve_807_sources(self):
        before = {str(i): 'a' * 64 for i in range(807)}
        c.sources_valid(before, dict(before, **{k: 'b' * 64 for k in c.ADDITIONS}))

    def test_predecessor_hash_change_rejected(self):
        before = {str(i): 'a' * 64 for i in range(807)}
        after = dict(before, **{k: 'b' * 64 for k in c.ADDITIONS})
        after['0'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'fence_source_drift'):
            c.sources_valid(before, after)

    def test_unregistered_extension_rejected(self):
        before = {str(i): 'a' * 64 for i in range(807)}
        with self.assertRaises(ValueError):
            c.sources_valid(before, dict(before, **{k: 'b' * 64 for k in c.ADDITIONS}, unknown='a' * 64))

    def test_new_fixed_names_not_predecessor_targets(self):
        self.assertNotEqual(w.DATABASE, w.host.DATABASE)
        self.assertNotEqual(w.SCHEMA, w.host.SCHEMA)
        self.assertNotEqual(w.LOGIN, w.host.LOGIN)
        self.assertEqual(len(set(w.ROLES)), 3)
        self.assertEqual(c.JOB, w.JOB_ID)
        self.assertLessEqual(len(w.JOB), 32)

    def test_frozen_schema_hash_and_additive_event_binding(self):
        raw = (ROOT / w.DDL_PATH).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), w.DDL_SHA)
        new = (ROOT / w.CONSTRAINT_PATH).read_text()
        self.assertIn('FOREIGN KEY (tenant_id,owner_object_id,task_id,worker_id)', new)
        self.assertIn('AND worker_id=$4 AND state=$5', new)
        self.assertNotIn('SECURITY DEFINER', new)
        self.assertNotIn('DROP ', new)

    def test_bounded_deterministic_dollar_free_bundle(self):
        value = plan()
        self.assertEqual(value, plan())
        args = value['definition']['properties']['template']['containers'][0]['args']
        self.assertNotIn(chr(36), w.canonical(args).decode())
        self.assertLessEqual(len(w.canonical(args)), 65536)
        text = unpack(value)
        compile(text, 'fence-packed-launch', 'exec')
        self.assertNotIn('run_owner_task_isolated_sql_root', text)
        self.assertNotIn('_sources[_migration]', text)
        self.assertIn('_sources[_ddl],_sources[_constraints]', text)
        self.assertLess(text.index('check_runtime()'), text.index('os.environ.pop("OWNERTRIAL_ADMIN_URL"'))

    def test_actual_module_graph_imports_with_no_repo_or_sql(self):
        text = unpack(plan())
        assignments = {n.targets[0].id: ast.literal_eval(n.value) for n in ast.parse(text).body
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in ('_sources', '_modules', '_hashes')}
        source, mods = assignments['_sources'], assignments['_modules']
        self.assertTrue(all(hashlib.sha256(v.encode()).hexdigest() == assignments['_hashes'][k] for k, v in source.items()))
        bootstrap = '''import sys,types,json,psycopg
def forbidden(*a,**k): raise RuntimeError("sql_during_import")
psycopg.connect=forbidden
pkg=types.ModuleType("ibkr_etl");pkg.__path__=[];sys.modules["ibkr_etl"]=pkg
'''
        bootstrap += 'sources=' + repr(source) + '\nmodules=' + repr(mods) + '\n'
        bootstrap += '''for path,name in modules:
 m=types.ModuleType(name);m.__file__=path;m.__package__="ibkr_etl" if name.startswith("ibkr_etl.") else ""
 sys.modules[name]=m
 if name.startswith("ibkr_etl."):setattr(pkg,name.rsplit(".",1)[1],m)
 exec(compile(sources[path],path,"exec"),m.__dict__)
print("module_graph_imported_without_sql")
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-I', '-B', '-c', 'exec(compile(__import__("sys").stdin.read(),"packed-import","exec"))'],
                input=bootstrap, cwd=directory, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'module_graph_imported_without_sql')

    def test_source_bytes_and_hash_drift_rejected(self):
        source = {path: (ROOT / path).read_bytes() for path in c.PATHS}
        hashes = {path: hashlib.sha256(value).hexdigest() for path, value in source.items()}
        source[w.PATH] += b'\n'
        with self.assertRaises(ValueError):
            c.build(source, hashes, runtime())

    def test_manual_single_execution_pinned_secret_identity_image(self):
        value = plan()['definition']
        config = value['properties']['configuration']
        self.assertEqual(config['triggerType'], 'Manual')
        self.assertEqual(config['replicaRetryLimit'], 0)
        self.assertEqual(config['manualTriggerConfig'], dict(parallelism=1, replicaCompletionCount=1))
        self.assertEqual(config['identitySettings'], [dict(identity=c.MI, lifecycle='None')])
        self.assertEqual(config['secrets'][0]['keyVaultUrl'], c.image.SECRET_VERSION_URL)
        self.assertEqual(value['properties']['template']['containers'][0]['image'], c.image.IMAGE)

    def native(self):
        value = plan()
        native = copy.deepcopy(value['definition'])
        native.update(id=c.JOB, name=w.JOB, type='Microsoft.App/jobs')
        identity = dict(clientId='synthetic-client', principalId='synthetic-principal')
        native['identity']['userAssignedIdentities'][c.MI] = identity
        native['properties']['provisioningState'] = 'Succeeded'
        return native, value, identity

    def test_exact_native_definition(self):
        native, value, identity = self.native()
        self.assertEqual(c.checked_job(native, value, identity)['definition_sha256'], value['definition_sha256'])

    def test_only_known_empty_native_aliases(self):
        native, value, identity = self.native()
        native['identity'].update(principalId=None, tenantId=None)
        native['properties'].update(workloadProfileName='Consumption')
        config = native['properties']['configuration']
        config.update(scheduleTriggerConfig=None, eventTriggerConfig=None, dapr=None)
        config['registries'][0].update(username='', passwordSecretRef='')
        template = native['properties']['template']
        template.update(initContainers=None, volumes=None)
        template['containers'][0]['resources']['ephemeralStorage'] = ''
        template['containers'][0]['env'][0]['secretRef'] = 'cappjob-' + w.host.SECRET_NAME
        c.checked_job(native, value, identity)

    def test_native_extra_identity_or_property_rejected(self):
        for location in ('root', 'identity', 'properties'):
            native, value, identity = self.native()
            target = native if location == 'root' else native[location]
            target['unreviewed'] = True
            with self.assertRaises(ValueError):
                c.checked_job(native, value, identity)

    def test_native_template_drift_rejected(self):
        native, value, identity = self.native()
        native['properties']['template']['containers'][0]['args'].append('unexpected')
        with self.assertRaises(ValueError):
            c.checked_job(native, value, identity)

    def test_non_null_volume_or_secret_default_rejected(self):
        native, value, identity = self.native()
        native['properties']['template']['volumes'] = []
        with self.assertRaises(ValueError):
            c.checked_job(native, value, identity)
        native, value, identity = self.native()
        native['properties']['configuration']['registries'][0]['passwordSecretRef'] = 'unreviewed-secret'
        with self.assertRaises(ValueError):
            c.checked_job(native, value, identity)

    def test_unregistered_get_or_mutation_routes_rejected_before_token(self):
        transport = c.Transport()
        with patch.object(transport.credential, 'get_token', side_effect=AssertionError('unexpected_auth')):
            for method, path, stage in [('GET', c.p.JOB, None), ('DELETE', c.JOB, 'job'), ('PUT', c.JOB, 'cleanup')]:
                with self.assertRaises(ValueError):
                    transport.call(method, path, stage=stage, plan=plan(), body={})

    def test_execution_subpath_and_malformed_name_rejected(self):
        transport = c.Transport()
        for name in ('wrong-abcdefg', w.JOB + '-abcdefg/replicas', w.JOB + '-ABCDEF0'):
            with self.assertRaises(ValueError):
                transport.call('GET', c.JOB + '/executions/' + name)

    def test_consumed_stage_rejected_without_remote_effect(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(c.p.old, 'FOLDER', Path(directory)):
            with (Path(directory) / 'serviceaud-step3-fence-start-intent.json').open('x') as stream:
                stream.write('{}')
            client = Mock()
            with self.assertRaisesRegex(ValueError, 'consumed'):
                c.dispatch(client, 'start')
            client.call.assert_not_called()

    def test_unregistered_database_rejected_before_connect(self):
        connector = Mock()
        with self.assertRaises(ValueError):
            w.connect_exact(connector, {}, 'production')
        connector.assert_not_called()

    def test_restricted_login_cannot_target_predecessor_database(self):
        connector = Mock()
        with self.assertRaises(ValueError):
            w.connect_exact(connector, {'user': w.LOGIN}, w.host.DATABASE, restricted=True)
        connector.assert_not_called()

    def test_new_transport_requires_full_certificate_validation(self):
        info = SimpleNamespace(host=w.host.HOST, dbname=w.DATABASE, user=w.LOGIN,
            get_parameters=lambda: dict(sslmode='require', sslrootcert=w.credential_parser.CA_PATH))
        raw = SimpleNamespace(info=info, pgconn=SimpleNamespace(ssl_in_use=True))
        with self.assertRaisesRegex(ValueError, 'fence_transport_invalid'):
            w.connect_exact(lambda **kwargs: raw, {'user': w.LOGIN}, w.DATABASE, restricted=True)

    def test_receipt_fixtures_do_not_claim_live_audit(self):
        item = w.work(1)
        fixture = w.synthetic_receipt(w.intent(item))
        self.assertEqual(fixture.namespace, w.AUDIT_NAMESPACE)
        self.assertIn('Explicit fixture', (ROOT / w.PATH).read_text())
        self.assertIn('protected_worker_receipts_verified=False', (ROOT / w.PATH).read_text())
        self.assertNotEqual(item.worker_id, item.owner.object_id)

    def test_invalid_ddl_produces_redacted_failure_without_sql(self):
        connector, output = Mock(), []
        value = w.main('synthetic-sensitive-secret', 'invalid', '', connect=connector,
            emit=lambda text, **kwargs: output.append(text))
        connector.assert_not_called()
        self.assertFalse(value['installation_attempted'])
        self.assertFalse(value['step_3_complete'])
        self.assertFalse(value['step_4_started'])
        self.assertEqual(value['source_rows_written'], 0)
        self.assertNotIn('synthetic-sensitive-secret', output[0])

    def test_unknown_sql_exception_is_not_exposed_or_retried(self):
        with patch.object(w.credential_parser, 'validate_admin_secret', return_value={}), \
                patch.object(w.credential_parser, 'require_ca_bundle'), \
                patch.object(w, 'connect_exact', side_effect=RuntimeError('secret details')):
            output = []
            value = w.main('sensitive', (ROOT / w.DDL_PATH).read_bytes().decode(), '', connect=Mock(),
                emit=lambda text, **kwargs: output.append(text))
        self.assertEqual(value['code'], 'fence_trial_unaccepted')
        self.assertEqual(value['error_class'], 'RuntimeError')
        self.assertFalse(value['automatic_retry'])
        self.assertNotIn('secret details', output[0])


if __name__ == '__main__':
    unittest.main()
