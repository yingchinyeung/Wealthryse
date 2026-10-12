"""Synthetic local guards only; no PostgreSQL/network/credential execution."""
import ast
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import psycopg

from ibkr_etl.owner_codex_tasks import OwnerTaskError

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT/'scripts/run_owner_task_restricted_trial.py'
spec = importlib.util.spec_from_file_location('owner_task_restricted_trial',PATH)
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)
INSTALLER = 'SYNTHETIC_TRUSTED_INSTALLER'


def permission_manifest():
    return dict(status='passed',permission_manifest_sha256='a'*64,column_privileges_verified=48,
        fresh_login_verified=True,metadata_acl_verified=True,end_user_authentication_verified=False,
        protected_receipts_verified=False,source_rows_read=0,source_rows_written=0,
        grants_executed=0,runtime_activated=False)


class RawFixture:
    def __init__(self):
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=0,host=trial.HOST,dbname=trial.DATABASE,user=trial.LOGIN)
        self.pgconn = SimpleNamespace(ssl_in_use=True)
        self.identity = dict(database=trial.DATABASE,role=trial.CAPABILITY,session_identity=trial.LOGIN,
            search_path=trial.SCHEMA+', pg_catalog',server_version='16.0',tenant_scope=None,
            server_version_num=160000,owner_scope=None,temp_schema=0)
        self.markers = dict.fromkeys(('database_marker','schema_marker','login_marker','capability_marker','owner_marker'),trial.MARKER)
        self.queries = []
        self.closed = False
        self.committed = self.rolled_back = self.fake_writes = 0
        self.failure = None
        self.active = False

    def execute(self,statement,parameters=(),**kwargs):
        text = statement if isinstance(statement,str) else statement.as_string()
        self.queries.append((text,parameters))
        if 'current_database()' in text: row = self.identity
        elif 'database_marker' in text: row = self.markers
        elif ' AS version' in text: row = {'version':'16.0'}
        elif text.startswith('SELECT active FROM'): row = {'active':self.active}
        elif 'FORBIDDEN_PROBE' in text:
            if self.failure: raise self.failure
            self.fake_writes += 1
            row = {}
        else: row = {}
        return SimpleNamespace(fetchone=lambda:row)

    def commit(self):
        self.committed += 1
        self.info.transaction_status = 0

    def rollback(self):
        self.rolled_back += 1
        self.info.transaction_status = 0

    def close(self):
        self.closed = True

    @contextmanager
    def transaction(self):
        saved_writes = self.fake_writes
        self.info.transaction_status = 2
        try:
            yield
        except Exception:
            self.fake_writes = saved_writes
            self.rollback()
            raise
        else:
            self.commit()


class ReadonlyAuthorityFixture:
    def __init__(self):
        self.owner_queries = []
        self.worker_queries = []
        self.owner_row = dict(active=True,role_name='owner',
            registered_subject_ids=['SYNTH_A','SYNTH_B'],authorized_subject_ids=['SYNTH_A','SYNTH_B'])
        self.worker_row = dict(service_principal_id=trial.SERVICE_PRINCIPAL,enabled=True)

    def execute(self,query,parameters=()):
        if 'owner_task_authority_fixture' in query:
            self.owner_queries.append((query,parameters))
            row = self.owner_row
        elif 'owner_task_worker_fixture' in query:
            self.worker_queries.append((query,parameters))
            row = self.worker_row
        else:
            raise AssertionError('only readonly authority fixture queries allowed')
        return SimpleNamespace(fetchone=lambda:row)


class OwnerTaskRestrictedTrialTests(unittest.TestCase):
    def test_import_is_inert_without_credentials_setup_cleanup_or_connection(self):
        source = PATH.read_text()
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(node,ast.If) for node in tree.body))
        self.assertNotRegex(source,r'psycopg\.connect\(|os\.environ|sys\.argv|subprocess|AzureCliCredential|requests\.')
        calls = [node for node in ast.walk(tree) if isinstance(node,ast.Call)]
        self.assertFalse(any(isinstance(node.func,ast.Attribute) and node.func.attr in
            ('grant_statements','upload_blob','start','send_mail') for node in calls))

    def test_exact_root_selected_registry(self):
        self.assertEqual((trial.DATABASE,trial.SCHEMA,trial.CAPABILITY,trial.LOGIN,trial.SCHEMA_OWNER,trial.MARKER),
            ('owner_codex_restricted_20261004','owner_codex_059_restricted','ibkr_owner_codex_dml_20261004',
             'ibkr_owner_codex_login_20261004','ibkr_owner_codex_schema_20261004',
             'wealthryse-owner-task-restricted-059-20261004-v1'))
        self.assertEqual(len(trial.CHECKS),8)
        self.assertEqual(len(set(trial.CHECKS)),8)

    def test_preflight_requires_fresh_registered_login_and_marked_non_owner_scope(self):
        raw = RawFixture()
        with mock.patch.object(trial,'_permission_manifest',return_value=permission_manifest()) as verifier:
            result = trial._preflight(raw,installer_identity=INSTALLER)
        verifier.assert_called_once_with(raw,installer_identity=INSTALLER)
        self.assertEqual(result,{'server_version':'16.0','permission_manifest_sha256':'a'*64})
        self.assertEqual(len(raw.queries),2)
        self.assertFalse(raw.closed)

    def test_admin_set_role_wrong_target_or_stale_scope_fails_before_permission_check(self):
        mutations = (
            lambda raw:setattr(raw.info,'user','TRUSTED_ADMIN'),
            lambda raw:setattr(raw.info,'host','wrong.example.com'),
            lambda raw:setattr(raw.info,'dbname','ibkr_staging'),
            lambda raw:setattr(raw,'autocommit',False),
            lambda raw:setattr(raw.info,'transaction_status',2),
            lambda raw:setattr(raw.pgconn,'ssl_in_use',False),
            lambda raw:setattr(raw.pgconn,'ssl_in_use',1),
            lambda raw:raw.identity.update(role=trial.SCHEMA_OWNER),
            lambda raw:raw.identity.update(session_identity='TRUSTED_ADMIN'),
            lambda raw:raw.identity.update(search_path='public, '+trial.SCHEMA),
            lambda raw:raw.identity.update(tenant_scope=trial.TENANT_A),
            lambda raw:raw.identity.update(owner_scope=trial.OWNER_A.object_id),
            lambda raw:raw.identity.update(temp_schema=123),
            lambda raw:raw.identity.update(server_version_num=150009),
            lambda raw:raw.identity.update(server_version_num=170000),
            lambda raw:raw.identity.update(server_version_num=True),
            lambda raw:raw.identity.update(database='postgres'))
        for mutate in mutations:
            raw = RawFixture()
            mutate(raw)
            with self.subTest(mutation=mutate),mock.patch.object(trial,'_permission_manifest') as verifier:
                with self.assertRaises(trial.TrialFailure): trial._preflight(raw,installer_identity=INSTALLER)
                verifier.assert_not_called()

    def test_missing_malformed_or_changed_database_schema_role_markers_rejected(self):
        for key in RawFixture().markers:
            for changed in ('other-marker',None,True):
                raw = RawFixture()
                raw.markers[key] = changed
                with self.subTest(key=key,changed=changed),mock.patch.object(trial,'_permission_manifest') as verifier:
                    with self.assertRaises(trial.TrialFailure): trial._preflight(raw,installer_identity=INSTALLER)
                    verifier.assert_not_called()
        raw = RawFixture()
        raw.markers['unknown'] = trial.MARKER
        with self.assertRaises(trial.TrialFailure): trial._preflight(raw,installer_identity=INSTALLER)

    def test_permission_adapter_requires_exact_typed_scope_only_manifest(self):
        from ibkr_etl import owner_codex_permissions as permissions
        raw = RawFixture()
        with mock.patch.object(permissions,'verify_permissions',return_value=permission_manifest()) as verifier:
            value = trial._permission_manifest(raw,installer_identity=INSTALLER)
        self.assertEqual(value,permission_manifest())
        self.assertEqual(verifier.call_count,1)
        self.assertEqual(verifier.call_args.kwargs,{'installer_identity':INSTALLER})
        for key,value in (('fresh_login_verified',1),('grants_executed',False),
            ('end_user_authentication_verified',True),('runtime_activated',True),
            ('column_privileges_verified',True),('column_privileges_verified',0),
            ('permission_manifest_sha256','F'*64),('unknown','field')):
            manifest = dict(permission_manifest(),**{key:value})
            with self.subTest(key=key,value=value),mock.patch.object(permissions,'verify_permissions',return_value=manifest):
                with self.assertRaises(trial.TrialFailure): trial._permission_manifest(raw,installer_identity=INSTALLER)

    def test_connection_wrapper_closes_and_rolls_back_on_failed_preflight(self):
        raw = RawFixture()
        raw.info.user = 'ADMIN'
        with self.assertRaises(trial.TrialFailure):
            with trial._connection(lambda:raw,installer_identity=INSTALLER): self.fail('yield must not happen')
        self.assertTrue(raw.closed)
        self.assertEqual(raw.rolled_back,1)

    def test_connection_wrapper_sets_only_tenant_then_closes_on_body_failure(self):
        raw = RawFixture()
        with mock.patch.object(trial,'_permission_manifest',return_value=permission_manifest()):
            with self.assertRaisesRegex(RuntimeError,'synthetic failure'):
                with trial._connection(lambda:raw,trial.TENANT_B,installer_identity=INSTALLER) as (seen,_):
                    self.assertIs(seen,raw)
                    self.assertIs(raw.autocommit,False)
                    raise RuntimeError('synthetic failure')
        self.assertTrue(raw.closed)
        self.assertEqual(raw.committed,1)
        self.assertEqual(raw.rolled_back,1)
        self.assertEqual(raw.queries[-1],("SELECT set_config('app.tenant_id',%s,false)",(trial.TENANT_B,)))

    def test_bind_tenant_refuses_dirty_pool_connection(self):
        raw = RawFixture()
        raw.info.transaction_status = 2
        with self.assertRaises(trial.TrialFailure): trial._bind_tenant(raw,'')
        self.assertEqual(raw.queries,[])

    def test_authority_worker_callbacks_query_fresh_exact_readonly_fixture(self):
        connection = ReadonlyAuthorityFixture()
        service = trial._service(connection,trial.SyntheticArchive(),lambda:datetime.now(timezone.utc))
        authority = service.authorize(trial.OWNER_A)
        self.assertEqual(authority.owner,trial.OWNER_A)
        self.assertEqual(authority.authorized_subject_ids,('SYNTH_A','SYNTH_B'))
        connection.owner_row = dict(connection.owner_row,active=False)
        self.assertIsNone(service.authorize(trial.OWNER_A))
        self.assertEqual(len(connection.owner_queries),2)
        self.assertEqual(connection.owner_queries[0][1],(trial.TENANT_A,trial.OWNER_A.object_id))
        worker = service.authorize_worker(trial.TENANT_A,trial.WORKER)
        self.assertEqual(worker.service_principal_id,trial.SERVICE_PRINCIPAL)
        connection.worker_row = dict(connection.worker_row,enabled=False)
        self.assertIsNone(service.authorize_worker(trial.TENANT_A,trial.WORKER))
        self.assertEqual(len(connection.worker_queries),2)
        self.assertTrue(all(query.lstrip().startswith('SELECT') for query,_ in
                            connection.owner_queries+connection.worker_queries))

    def test_authority_callbacks_reject_unregistered_subject_sets_worker_and_owner(self):
        connection = ReadonlyAuthorityFixture()
        service = trial._service(connection,trial.SyntheticArchive(),lambda:datetime.now(timezone.utc))
        connection.owner_row['authorized_subject_ids'] = ['SYNTH_A','SYNTH_UNREGISTERED']
        with self.assertRaises(trial.TrialFailure): service.authorize(trial.OWNER_A)
        with self.assertRaises(trial.TrialFailure): service.authorize_worker(trial.TENANT_A,'untrusted-worker')
        connection.worker_row['service_principal_id'] = trial.OWNER_A.object_id
        with self.assertRaises(trial.TrialFailure): service.authorize_worker(trial.TENANT_A,trial.WORKER)

    def test_synthetic_receipt_binding_is_explicit_and_rejects_rebinding(self):
        archive = trial.SyntheticArchive()
        binding = {'synthetic':'value'}
        reference = archive.add(binding)
        self.assertEqual(archive(reference,binding)['namespace'],'owner_codex_task_v1')
        with self.assertRaises(trial.TrialFailure): archive(reference,{'synthetic':'changed'})
        with self.assertRaises(trial.TrialFailure): archive('f'*64,binding)

    def test_sql_privilege_denial_is_verified_and_scope_transaction_closed(self):
        raw = RawFixture()
        raw.failure = psycopg.errors.InsufficientPrivilege('synthetic provider message')
        trial._sql_denied(raw,'FORBIDDEN_PROBE')
        self.assertEqual(raw.info.transaction_status,0)
        self.assertEqual(raw.fake_writes,0)
        self.assertEqual(raw.rolled_back,1)

    def test_unexpected_sql_success_rolls_back_before_fixed_denial_failure(self):
        raw = RawFixture()
        with self.assertRaises(trial.TrialFailure) as error:
            trial._sql_denied(raw,'FORBIDDEN_PROBE')
        self.assertEqual(error.exception.code,'sql_denial_not_enforced')
        self.assertEqual(raw.fake_writes,0)
        self.assertEqual(raw.rolled_back,1)
        self.assertEqual(raw.info.transaction_status,0)
        self.assertEqual(raw.queries[-1][0],'SET CONSTRAINTS ALL IMMEDIATE')

    def test_wrong_sqlstate_is_not_misclassified_as_permission_denial(self):
        raw = RawFixture()
        raw.failure = psycopg.errors.SyntaxError('synthetic syntax message')
        with self.assertRaises(trial.TrialFailure) as error:
            trial._sql_denied(raw,'FORBIDDEN_PROBE')
        self.assertEqual(error.exception.code,'unexpected_sql_denial')
        self.assertNotIn('synthetic syntax',str(error.exception))

    def test_row_security_off_probe_must_fail_with_permission_denial_not_bypass(self):
        raw = RawFixture()
        raw.failure = psycopg.errors.InsufficientPrivilege('forced RLS synthetic fixture')
        trial._sql_denied(raw,'FORBIDDEN_PROBE',row_security_off=True)
        self.assertIn(('SET LOCAL row_security=off',()),raw.queries)
        self.assertEqual(raw.rolled_back,1)
        self.assertEqual(raw.info.transaction_status,0)
        raw = RawFixture()
        with self.assertRaises(trial.TrialFailure) as error:
            trial._sql_denied(raw,'FORBIDDEN_PROBE',row_security_off=True)
        self.assertEqual(error.exception.code,'sql_denial_not_enforced')
        self.assertEqual(raw.fake_writes,0)
        self.assertEqual(raw.rolled_back,1)
        self.assertEqual(raw.info.transaction_status,0)

    def test_fixed_privilege_probe_catalog_includes_replication_role_and_forced_rls(self):
        raw = RawFixture()
        @contextmanager
        def connection(_,*,installer_identity): yield raw,'synthetic-adapter'
        task = {'task_id':'00000000-0000-0000-0000-000000000401'}
        with (mock.patch.object(trial,'_connection',side_effect=connection),
              mock.patch.object(trial,'_sql_denied') as denied,
              mock.patch.object(trial,'_row',return_value={'state':'queued','revision':1})):
            count = trial._privilege_denials(lambda:None,task,installer_identity=INSTALLER)
        self.assertEqual(count,21)
        statements = [call.args[1].as_string() for call in denied.call_args_list]
        self.assertIn('SET LOCAL session_replication_role=replica',statements)
        self.assertEqual(sum(call.kwargs.get('row_security_off') is True for call in denied.call_args_list),1)
        self.assertEqual(denied.call_args_list[-1].kwargs['states'],('P0001',))

    def test_service_denial_requires_exact_fixed_error(self):
        def denied(): raise OwnerTaskError('access_denied')
        trial._denied(denied,'access_denied')
        with self.assertRaises(trial.TrialFailure): trial._denied(denied,'not_found')
        with self.assertRaises(trial.TrialFailure): trial._denied(lambda:None,'access_denied')

    def test_revocation_acknowledgment_has_exact_independent_contract(self):
        expected = dict(status='passed',database='owner_codex_restricted_20261004',schema='owner_codex_059_restricted',
            marker='wealthryse-owner-task-restricted-059-20261004-v1',fixture='owner_task_authority_fixture',
            tenant_id='00000000-0000-0000-0000-000000000001',owner_object_id='00000000-0000-0000-0000-000000000201',
            active=False,rows_changed=1,mutation_role='ibkr_owner_codex_schema_20261004',synthetic=True)
        self.assertEqual(trial.expected_revocation(),expected)
        self.assertEqual(len(expected),11)

    def _revocation_fixture(self,acknowledgment,*,active=False):
        raw = RawFixture()
        raw.active = active
        service = mock.Mock()
        service.get.side_effect = [{'state':'queued'},OwnerTaskError('access_denied'),OwnerTaskError('access_denied')]
        service.cancel.side_effect = OwnerTaskError('access_denied')
        service.create_session.side_effect = OwnerTaskError('access_denied')
        @contextmanager
        def connection(_,*,installer_identity):
            self.assertEqual(installer_identity,INSTALLER)
            yield raw,'synthetic-adapter'
        callback = mock.Mock(return_value=acknowledgment)
        with (mock.patch.object(trial,'_connection',side_effect=connection),
              mock.patch.object(trial,'_service',return_value=service)):
            trial._revocation(lambda:None,callback,trial.SyntheticArchive(),lambda:datetime.now(timezone.utc),
                              {'task_id':'00000000-0000-0000-0000-000000000401'},installer_identity=INSTALLER)
        return raw,callback,service

    def test_revocation_callback_is_zero_argument_once_and_independently_reread(self):
        raw,callback,service = self._revocation_fixture(trial.expected_revocation())
        callback.assert_called_once_with()
        self.assertTrue(any(query.startswith('SELECT active FROM') for query,_ in raw.queries))
        self.assertEqual(service.get.call_count,3)
        self.assertEqual(service.cancel.call_count,1)
        self.assertEqual(service.create_session.call_count,1)
        self.assertFalse(any(query.lstrip().startswith('UPDATE') for query,_ in raw.queries))

    def test_revocation_bad_acknowledgment_or_unrevoked_database_cannot_pass(self):
        for key,value in (('active',0),('rows_changed',True),('mutation_role',trial.CAPABILITY),
            ('database','ibkr_staging'),('synthetic',1),('unknown','field')):
            with self.subTest(key=key,value=value):
                changed = dict(trial.expected_revocation(),**{key:value})
                with self.assertRaises(trial.TrialFailure): self._revocation_fixture(changed)
        with self.assertRaises(trial.TrialFailure):
            self._revocation_fixture(trial.expected_revocation(),active=True)

    def _orchestrate(self,*,failed_step=None,changed_final=False):
        raw = RawFixture()
        @contextmanager
        def connection(_,*,installer_identity):
            self.assertEqual(installer_identity,INSTALLER)
            yield raw,'synthetic-adapter'
        metadata = [dict(server_version='16.0',permission_manifest_sha256='a'*64),
                    dict(server_version='16.0',permission_manifest_sha256=('b' if changed_final else 'a')*64)]
        def phase(name,result=None):
            def operation(*args,**kwargs):
                if failed_step == name: raise RuntimeError('provider text and credentials must not escape')
                return result
            return operation
        factory,revocation = mock.Mock(),mock.Mock()
        with (mock.patch.object(trial,'_connection',side_effect=connection),
              mock.patch.object(trial,'_permission_manifest_version',side_effect=metadata),
              mock.patch.object(trial,'_crud_and_scopes',side_effect=phase('crud',{'task_id':'synthetic'})) as crud,
              mock.patch.object(trial,'_privilege_denials',side_effect=phase('privileges',19)) as privileges,
              mock.patch.object(trial,'_idempotency_and_cas',side_effect=phase('cas')) as cas,
              mock.patch.object(trial,'_cancelled_synthetic_outcome',side_effect=phase('cancel')) as cancel,
              mock.patch.object(trial,'_revocation',side_effect=phase('revocation')) as revoke):
            result = trial.verify_trial(factory,revoke_authority=revocation,installer_identity=INSTALLER)
        return result,[crud,privileges,cas,cancel,revoke]

    def test_complete_synthetic_orchestration_never_claims_owner_model_archive_or_source_proof(self):
        result,phases = self._orchestrate()
        self.assertEqual(result['status'],'passed')
        self.assertEqual(result['checks'],list(trial.CHECKS))
        self.assertTrue(result['fresh_login_verified'])
        self.assertTrue(result['non_owner_privilege_boundary_verified'])
        for key in ('signed_owner_authentication_verified','protected_archive_live_verified',
            'managed_identity_verified','model_credential_containment_verified','production_source_access_acceptance',
            'production_migration_installed','cleanup_performed','automatic_retry'):
            self.assertIs(result[key],False)
        for key in ('source_rows_read','source_rows_written','model_calls','agents_activated',
                    'service_grants_created','capability_authority_fixture_writes'):
            self.assertIs(type(result[key]),int)
            self.assertEqual(result[key],0)
        self.assertTrue(result['receipt_verifier_is_synthetic'])
        self.assertEqual([phase.call_count for phase in phases],[1]*5)

    def test_failed_stage_has_no_auto_retry_no_following_phases_and_no_provider_text(self):
        for index,name in enumerate(('crud','privileges','cas','cancel','revocation')):
            with self.subTest(name=name):
                result,phases = self._orchestrate(failed_step=name)
                self.assertEqual(result['status'],'failed')
                self.assertEqual(result['reason'],'restricted_postgresql_trial_failed')
                self.assertEqual([phase.call_count for phase in phases],[1]*(index+1)+[0]*(4-index))
                self.assertFalse(result['fresh_login_verified'])
                self.assertFalse(result['fresh_database_authority_revocation_verified'])
                self.assertNotIn('credentials',str(result))

    def test_final_permission_manifest_drift_rejects_acceptance(self):
        result,_ = self._orchestrate(changed_final=True)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['reason'],'final_permission_manifest_differs')
        self.assertFalse(result['non_owner_privilege_boundary_verified'])

    def test_missing_required_callbacks_fail_before_any_connection(self):
        factory = mock.Mock()
        result = trial.verify_trial(factory,revoke_authority=None,installer_identity=INSTALLER)
        factory.assert_not_called()
        self.assertEqual(result['reason'],'trusted_callbacks_required')
        self.assertEqual(result['checks'],[])

    def test_installer_identity_is_mandatory_and_never_derived_from_login_or_runtime(self):
        factory = mock.Mock()
        with self.assertRaises(TypeError):
            trial.verify_trial(factory,revoke_authority=lambda:None)
        for installer in (None,trial.LOGIN,trial.CAPABILITY,trial.SCHEMA_OWNER,'arbitrary role;',1):
            with self.subTest(installer=installer):
                result = trial.verify_trial(factory,revoke_authority=lambda:None,installer_identity=installer)
                self.assertEqual(result['reason'],'trusted_installer_identity_required')
                self.assertEqual(result['checks'],[])
        factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
