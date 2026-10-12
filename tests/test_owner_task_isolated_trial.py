"""Offline guards for the additive private-host trial; not live SQL evidence."""
import ast
import copy
import importlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import owner_task_isolated_permissions as permissions
import prepare_owner_task_isolated_trial as prep
import run_owner_task_isolated_trial as trial
import owner_task_azure_database_boundary as boundary
from ibkr_etl import owner_codex_isolated_target as target
import test_owner_codex_permissions as original


def snapshot(enabled=True):
    mapping = dict(zip((original.DATABASE, original.SCHEMA, original.OWNER,
        original.CAP, original.LOGIN, original.INSTALLER,
        'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'),
        (target.DATABASE, target.SCHEMA, target.SCHEMA_OWNER, target.CAPABILITY,
         target.LOGIN, target.ADMIN, target.HOST)))
    def remap(value):
        if type(value) is dict: return {k: remap(v) for k, v in value.items()}
        if type(value) is list: return [remap(v) for v in value]
        if type(value) is str:
            for before, after in mapping.items(): value = value.replace(before, after)
        return value
    result = remap(original.catalog(login_enabled=enabled))
    result['bootstrap_role'] = 'azuresu'
    for row in result['memberships']:
        if row['grantor'] == 'postgres': row['grantor'] = 'azuresu'
    result['databases'] = [dict(role=role, database=db,
        connect=db in boundary.PROVIDER_DATABASES or db == target.DATABASE and role == target.LOGIN,
        create=False, temporary=db in boundary.PROVIDER_DATABASES)
        for role in (target.CAPABILITY, target.LOGIN) for db in
        sorted(boundary.PROVIDER_DATABASES | boundary.CUSTOMER_DATABASES | {target.DATABASE})]
    return result


def validate(value, **kwargs):
    return permissions.validate_catalog(value, permissions.PermissionTarget(),
        installer_identity=target.ADMIN, **kwargs)


class IsolatedPermissionTests(unittest.TestCase):
    def test_valid_complete_private_catalog(self):
        result = validate(snapshot())
        self.assertEqual(result['column_privileges_verified'], 84)
        self.assertTrue(result['fresh_login_verified'])
        self.assertFalse(result['runtime_activated'])

    def test_target_and_old_constants_are_not_mutated(self):
        from ibkr_etl import owner_codex_permissions as old
        self.assertNotEqual(old.HOST, permissions.HOST)
        self.assertEqual(permissions.HOST, target.HOST)
        for module in (prep, trial):
            self.assertEqual(module.DATABASE, target.DATABASE)
            self.assertEqual(module.MARKER, target.MARKER)
        self.assertEqual(permissions.SELECT, old.SELECT)
        self.assertEqual(permissions.INSERT, old.INSERT)
        self.assertEqual(permissions.UPDATE, old.UPDATE)

    def test_unregistered_target_and_extra_attributes_rejected(self):
        for field in ('database', 'schema', 'schema_owner', 'capability', 'login'):
            with self.subTest(field=field), self.assertRaises(permissions.PermissionDenied):
                permissions.PermissionTarget(**{field: 'other'})
        forged = permissions.PermissionTarget()
        object.__setattr__(forged, 'unreviewed', True)
        with self.assertRaises(permissions.PermissionDenied): permissions.grant_statements(forged)

    def test_installer_must_be_exact_private_admin(self):
        for value in ('trusted_installer', target.LOGIN, target.SCHEMA_OWNER, '', None):
            with self.subTest(value=value), self.assertRaises(permissions.PermissionDenied):
                permissions.validate_catalog(snapshot(), permissions.PermissionTarget(), installer_identity=value)
            with self.assertRaises(trial.TrialFailure): trial._require_installer(value)

    def test_fresh_login_not_admin_set_role(self):
        for field in ('authenticated_user', 'session_user', 'current_user', 'host', 'database', 'search_path'):
            value = snapshot(); value['identity'][field] = target.ADMIN
            with self.subTest(field=field), self.assertRaises(permissions.PermissionDenied): validate(value)

    def test_customer_connect_temp_create_and_provider_create_fail(self):
        for index, row in enumerate(snapshot()['databases']):
            flags = ('create',) if row['database'] in boundary.PROVIDER_DATABASES else ('create', 'temporary')
            if row['database'] in boundary.CUSTOMER_DATABASES: flags += ('connect',)
            for flag in flags:
                value = snapshot(); value['databases'][index][flag] = True
                with self.subTest(database=row['database'], role=row['role'], flag=flag), self.assertRaises(permissions.PermissionDenied):
                    validate(value)

    def test_unknown_missing_duplicate_database_and_boolean_alias_fail(self):
        for mutation in (lambda r: r.pop(), lambda r: r.append(copy.deepcopy(r[0])),
                         lambda r: r[0].update(database='unreviewed'), lambda r: r[0].update(create=0)):
            value = snapshot(); mutation(value['databases'])
            with self.assertRaises(permissions.PermissionDenied): validate(value)

    def test_no_rls_owner_ddl_or_membership_weakening(self):
        changes = [('roles', 0, 'rolsuper', True), ('roles', 1, 'rolinherit', True),
            ('memberships', 0, 'admin_option', True), ('memberships', 0, 'inherit_option', True),
            ('task_relations', 0, 'force_rls', False), ('task_relations', 0, 'rls', False),
            ('schemas', 0, 'create', True), ('functions', 0, 'security_definer', True)]
        for collection, index, field, altered in changes:
            value = snapshot(); value[collection][index][field] = altered
            with self.subTest(field=field), self.assertRaises(permissions.PermissionDenied): validate(value)

    def test_grant_manifest_exact_column_only(self):
        statements = [s.as_string() for s in permissions.grant_statements(permissions.PermissionTarget())]
        self.assertEqual(len(statements), 11)
        self.assertTrue(all(target.CAPABILITY in s for s in statements))
        self.assertNotRegex('\n'.join(statements), 'GRANT ALL|GRANT OPTION|CREATE ROLE|PASSWORD|CONNECT|tenants')

    def test_prelogin_does_not_claim_fresh_login(self):
        value = snapshot(False)
        value['identity'].update(authenticated_user=target.ADMIN, session_user=target.ADMIN,
            current_user=target.ADMIN, search_path='pg_catalog')
        self.assertFalse(validate(value, require_fresh_login=False, login_enabled=False)['fresh_login_verified'])

    def test_inert_adapters_no_connector_or_credentials_on_import(self):
        for name in ('owner_task_isolated_permissions', 'prepare_owner_task_isolated_trial', 'run_owner_task_isolated_trial'):
            source = (ROOT / 'scripts' / (name + '.py')).read_text(encoding='utf-8')
            self.assertFalse(any(isinstance(n, ast.If) for n in ast.parse(source).body))
            self.assertNotRegex(source, r'psycopg\.connect\(|os\.environ|requests\.|subprocess\.|sys\.argv|print\(')
            self.assertNotIn('DROP DATABASE', source)

    def test_migration_unchanged_password_shape_and_fail_closed(self):
        content = (ROOT / 'migrations/postgres/059_owner_codex_tasks.sql').read_text(encoding='utf-8')
        prep.validate_inputs(content, 'SyntheticOnly_' + 'x' * 64)
        for migration, password in ((content + ' ', 'x' * 64), (content, 'short'), (content, 'x;' * 40)):
            with self.assertRaises(prep.PreparationError): prep.validate_inputs(migration, password)

    def test_catalog_recheck_is_readonly_idle_and_restores_after_failure(self):
        admin = SimpleNamespace(autocommit=True, info=SimpleNamespace(transaction_status=0), execute=Mock())
        sentinel = object()
        with (patch.object(prep.collector, 'inspect_provider', return_value=sentinel) as inspect,
              patch.object(prep.boundary, 'review_catalog') as review):
            self.assertIs(prep.assert_public_database_boundary(admin), sentinel)
            inspect.assert_called_once_with(admin); review.assert_called_once_with(sentinel)
        self.assertEqual([c.args[0] for c in admin.execute.call_args_list],
            ['SET search_path=pg_catalog', 'SET default_transaction_read_only=on', 'SET default_transaction_read_only=off'])
        admin.execute.reset_mock()
        with patch.object(prep.collector, 'inspect_provider', side_effect=ValueError('synthetic')):
            with self.assertRaises(ValueError): prep.assert_public_database_boundary(admin)
        self.assertEqual(admin.execute.call_args_list[-1].args, ('SET default_transaction_read_only=off',))

    def test_public_schema_revoke_before_owner_switch_and_wrong_owner_fails(self):
        row = dict(session_user=target.ADMIN, current_user=target.ADMIN, public_schema_owner='azure_pg_admin')
        class Raw:
            info = SimpleNamespace(host=target.HOST, dbname=target.DATABASE)
            def __init__(self): self.queries = []
            def execute(self, statement, *args):
                self.queries.append(statement if type(statement) is str else statement.as_string())
                return SimpleNamespace(fetchone=lambda: dict(row))
        raw = Raw(); prep._initialize(raw, '-- synthetic migration')
        self.assertEqual(raw.queries[1], 'REVOKE ALL ON SCHEMA public FROM PUBLIC')
        self.assertEqual(raw.queries[2], 'SET LOCAL ROLE "' + target.SCHEMA_OWNER + '"')
        row['public_schema_owner'] = 'pg_database_owner'; raw = Raw()
        with self.assertRaises(boundary.BoundaryError): prep._initialize(raw, '-- synthetic migration')
        self.assertEqual(len(raw.queries), 1)

    def test_wrong_underlying_admin_session_rejected_before_effects(self):
        raw = SimpleNamespace(autocommit=True, info=SimpleNamespace(transaction_status=0,
            host=target.HOST, dbname='postgres', user='other'), pgconn=SimpleNamespace(ssl_in_use=True),
            execute=Mock(return_value=SimpleNamespace(fetchone=lambda: dict(database='postgres', role='other', login='other', version='160015'))))
        with self.assertRaises(prep.PreparationError): prep.bind_trusted_connection(raw, 'postgres')

    def test_failed_harness_never_reports_completion_or_isolation(self):
        result = trial.verify_trial(lambda: None, revoke_authority=lambda: None, installer_identity='other')
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(result['fresh_login_verified'])
        self.assertFalse(result['model_credential_containment_verified'])
        self.assertEqual(result['source_rows_written'], 0)


if __name__ == '__main__': unittest.main()
