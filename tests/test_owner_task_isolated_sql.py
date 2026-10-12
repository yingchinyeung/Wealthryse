"""Offline source/launch/one-shot guards; no cloud or live SQL acceptance."""
import base64
import copy
import hashlib
import json
import lzma
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_owner_task_isolated_sql as worker
import owner_task_isolated_sql_launch as launch
import deploy_owner_task_isolated_sql as control
from ibkr_etl import owner_codex_isolated_target as target


def runtime():
    return dict(schema_version=1, observation='image_runtime_metadata_only', python_implementation='CPython',
        python_version='3.12.14', system='Linux', machine='x86_64', pointer_bits=64, effective_uid=10001,
        effective_gid=10001, psycopg_version='3.3.6', psycopg_binary_version='3.3.6', pq_implementation='binary',
        libpq_version=180006, libpq_build_version=180006, ca_path=launch.runtime.CA_PATH, ca_bytes=224449,
        ca_sha256='714d457d580922dbf1d0be8bd35ba236a842b50b0072ae791582a19adef772a5', ca_certificates=150,
        pg_environment_absent=True, **{k: False for k in launch.runtime.FALSE_FLAGS},
        **{k: 0 for k in launch.runtime.ZERO_COUNTS})


def plan():
    source = {k: (ROOT / k).read_bytes() for k in launch.SOURCE_PATHS}
    return launch.build(source, {k: hashlib.sha256(v).hexdigest() for k, v in source.items()}, runtime())


class SQLLaunchTests(unittest.TestCase):
    def test_exact_14_source_bundle_and_deterministic_bounded_arguments(self):
        p = plan(); self.assertEqual(p, plan())
        self.assertEqual(len(p['source_hashes']), 14)
        args = p['definition']['properties']['template']['containers'][0]['args']
        self.assertEqual(args[:3], ['-I', '-B', '-c'])
        self.assertLessEqual(len(launch.canonical(args)), 65536)
        packed = args[3].split("base64.b64decode('", 1)[1].split("')", 1)[0]
        source = lzma.decompress(base64.b64decode(packed)).decode()
        self.assertLess(source.index('check_runtime()'), source.index('os.environ.pop("OWNERTRIAL_ADMIN_URL"'))
        self.assertNotIn('psycopg.connect(', launch.LAUNCHER)

    def test_exact_manual_job_private_identity_pinned_secret_no_retries(self):
        d = plan()['definition']; config = d['properties']['configuration']
        self.assertEqual(d['identity']['userAssignedIdentities'], {worker.IDENTITY_ID: {}})
        self.assertEqual(config['identitySettings'], [dict(identity=worker.IDENTITY_ID, lifecycle='None')])
        self.assertEqual(config['triggerType'], 'Manual'); self.assertEqual(config['replicaRetryLimit'], 0)
        self.assertEqual(config['manualTriggerConfig'], dict(parallelism=1, replicaCompletionCount=1))
        self.assertEqual(config['secrets'], [dict(name=target.SECRET_NAME, keyVaultUrl=launch.prior.SECRET_VERSION_URL, identity=worker.IDENTITY_ID)])
        self.assertEqual(d['properties']['template']['containers'][0]['env'], [dict(name='OWNERTRIAL_ADMIN_URL', secretRef=target.SECRET_NAME)])
        self.assertFalse(plan()['step_2_complete'])

    def test_missing_extra_changed_source_and_migration_rejected(self):
        s = {k: (ROOT / k).read_bytes() for k in launch.SOURCE_PATHS}
        h = {k: hashlib.sha256(v).hexdigest() for k, v in s.items()}
        for mode in ('missing', 'extra', 'bytes', 'migration'):
            source, hashes = dict(s), dict(h)
            if mode == 'missing': source.pop(next(iter(source)))
            if mode == 'extra': source['unknown'] = b'pass'; hashes['unknown'] = 'a' * 64
            if mode == 'bytes': source[next(iter(source))] += b' '
            if mode == 'migration':
                source[launch.MIGRATION] += b' '; hashes[launch.MIGRATION] = hashlib.sha256(source[launch.MIGRATION]).hexdigest()
            with self.subTest(mode=mode), self.assertRaises(Exception): launch.build(source, hashes, runtime())

    def test_only_registered_database_connector_and_hard_tls(self):
        connect = Mock(); params = dict(host=target.HOST, user=target.LOGIN, password='SyntheticOnly',
            dbname=target.DATABASE, port='5432', sslmode='verify-full', sslrootcert=worker.CA_PATH)
        for database in ('ibkr_staging', 'production', '', None):
            with self.assertRaises(ValueError): worker._connect(connect, params, database)
        connect.assert_not_called()
        worker._connect(connect, params, target.DATABASE, restricted=True)
        actual = connect.call_args.kwargs
        self.assertEqual(actual['sslmode'], 'verify-full'); self.assertEqual(actual['sslrootcert'], worker.CA_PATH)
        self.assertIn('-c role=' + target.CAPABILITY, actual['options'])

    def test_wrong_hostname_probe_rejects_network_wrong_password_and_success(self):
        for error in (ValueError('network unavailable'), ValueError('password authentication failed')):
            result = worker._hostname_probe(Mock(side_effect=error), {}, '10.0.0.4')
            self.assertEqual(result['status'], 'unaccepted')
        connect = Mock(return_value=SimpleNamespace(close=Mock()))
        self.assertEqual(worker._hostname_probe(connect, {}, '10.0.0.4')['status'], 'unaccepted')
        connect.return_value.close.assert_called_once()
        error = ValueError('server certificate does not match host name "ownertrial-invalid.example.test"')
        result = worker._hostname_probe(Mock(side_effect=error), {}, '10.0.0.4')
        self.assertEqual(result['status'], 'passed'); self.assertNotIn(str(error), json.dumps(result))

    def test_provider_connected_is_not_denial_and_no_query_store_read(self):
        class Raw:
            def __init__(self, db): self.db = db; self.queries = []; self.closed = False
            def execute(self, query):
                self.queries.append(query)
                return SimpleNamespace(fetchone=lambda: dict(login=target.LOGIN, database=self.db))
            def close(self): self.closed = True
        raws = []
        def connect(*args, **kwargs):
            raw = Raw(kwargs['dbname']); raws.append(raw); return raw
        with patch.object(worker, '_transport'):
            probes = worker._provider_probes(connect, {})
        self.assertTrue(all(p['outcome'] == 'connected_requires_object_review' for p in probes))
        self.assertTrue(all(len(raw.queries) == 1 and raw.closed for raw in raws))
        with self.assertRaises(worker.boundary.BoundaryError): worker.boundary.validate_provider_behavior(probes)

    def test_provider_generic_error_never_fabricates_sqlstate(self):
        probes = worker._provider_probes(Mock(side_effect=ValueError('SyntheticCredential unavailable')), {})
        self.assertTrue(all(p['outcome'] == 'unclassified_failure' and p['sqlstate'] == '' for p in probes))
        self.assertNotIn('SyntheticCredential', json.dumps(probes))

    def test_bad_secret_no_connector_and_no_sensitive_error_output(self):
        connect = Mock(); result = worker.run('SensitiveSyntheticValue', 'bad migration', connect=connect)
        connect.assert_not_called(); self.assertEqual(result['status'], 'unaccepted')
        self.assertFalse(result['installation_attempted']); self.assertFalse(result['step_2_complete'])
        self.assertFalse(result['model_credential_isolation_verified'])
        self.assertNotIn('SensitiveSyntheticValue', json.dumps(result))

    def test_controller_accepts_exact_752_plus_eight_without_retargeting(self):
        before = {f'old/{i}.py': 'a' * 64 for i in range(752)}
        after = dict(before, **{k: 'b' * 64 for k in control.ADDITIONS})
        control.validate_sources(before, after)
        for mode in ('changed', 'missing', 'extra'):
            wrong = dict(after)
            if mode == 'changed': wrong['old/0.py'] = 'c' * 64
            if mode == 'missing': wrong.pop(next(iter(control.ADDITIONS)))
            if mode == 'extra': wrong['unreviewed.py'] = 'd' * 64
            with self.assertRaises(Exception): control.validate_sources(before, wrong)

    def test_effects_registered_scopes_only(self):
        p = plan(); principal = '11111111-1111-4111-8111-111111111111'
        self.assertEqual(control.effect('identity', p)[:3], ('PUT', worker.IDENTITY_ID, '2023-01-31'))
        for stage in control.ROLES:
            effect = control.effect(stage, p, principal)
            self.assertEqual(effect[3]['properties']['principalType'], 'ServicePrincipal')
            self.assertNotIn('/resourceGroups/', effect[3]['properties']['roleDefinitionId'])
        self.assertEqual(control.effect('job', p)[1], worker.JOB_ID)
        self.assertEqual(control.effect('start', p)[0:2], ('POST', worker.JOB_ID + '/start'))
        for stage in ('production', 'rollback', 'delete', None):
            with self.assertRaises(Exception): control.effect(stage, p, principal)

    def test_bad_routes_rejected_before_http_or_token(self):
        client = object.__new__(control.Transport); client.credential = Mock(); client.session = Mock(); client.token = None
        for method, path, api in (('DELETE', worker.JOB_ID, '2025-07-01'),
            ('GET', target.SERVER_ID, '2024-08-01'), ('POST', target.JOB_ID + '/start', '2025-07-01')):
            with self.assertRaises(Exception): client.call(method, path, api)
        client.session.request.assert_not_called(); client.credential.get_token.assert_not_called()

    def test_execution_name_is_fixed_job_only(self):
        self.assertEqual(control.execution_name(worker.JOB + '-abcdefgh'), worker.JOB + '-abcdefgh')
        for value in (target.JOB + '-abcdefgh', '../start', worker.JOB, None):
            with self.assertRaises(Exception): control.execution_name(value)

    def test_one_shot_dispatch_checks_consumed_before_load_or_network(self):
        with patch.object(Path, 'exists', return_value=True), patch.object(control, 'load') as load:
            with self.assertRaises(Exception): control.dispatch(Mock(), 'start')
            load.assert_not_called()

    def test_readback_rejects_identity_runtime_access_and_extra_env(self):
        p = plan(); identity = dict(principalId='11111111-1111-4111-8111-111111111111', clientId='22222222-2222-4222-8222-222222222222')
        value = copy.deepcopy(p['definition']); value.update(id=worker.JOB_ID, name=worker.JOB, type='Microsoft.App/jobs')
        value['identity']['userAssignedIdentities'][worker.IDENTITY_ID] = identity
        value['properties']['provisioningState'] = 'Succeeded'
        control.validate_job(value, p, identity)
        for mutation in (lambda v: v['properties']['configuration']['identitySettings'][0].update(lifecycle='All'),
            lambda v: v['properties']['template']['containers'][0]['env'].append(dict(name='PGPASSWORD', value='Synthetic')),
            lambda v: v['properties']['configuration'].update(replicaRetryLimit=1)):
            wrong = copy.deepcopy(value); mutation(wrong)
            with self.assertRaises(Exception): control.validate_job(wrong, p, identity)


if __name__ == '__main__': unittest.main()
