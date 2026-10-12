"""Exact empty-scope cleanup guards, not live database acceptance."""
import base64
import copy
import gzip
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import cleanup_owner_task_failed_setup as cleanup
from test_owner_task_postgres_deployment import job


def metadata():
    value = dict(status='observed',trial_database_exists=True,trial_role_exists=True,
        database_marker_matches=True,database_owner_matches=True,role_marker_matches=True,
        production_database_touched=False,ibkr_source_writes=False,ddl_executed=False,
        existing_service_grants_changed=False,model_calls=0,outgoing_memberships=0,
        permanent_relation_count=0,task_function_count=0,
        role_attributes=dict.fromkeys(('rolcanlogin','rolinherit','rolsuper','rolcreatedb',
            'rolcreaterole','rolreplication','rolbypassrls'),False),
        schema_inventory=[dict(registered=True,trial_schema=True,trial_owner=True),
            dict(registered=True,trial_schema=False,trial_owner=False)],
        memberships=[dict(member_is_admin=True,member_is_azure_admin=False,grantor_is_admin=False,
            grantor_is_bootstrap=True,admin_option=True,inherit_option=False,set_option=False),
            dict(member_is_admin=True,member_is_azure_admin=False,grantor_is_admin=True,
                grantor_is_bootstrap=False,admin_option=False,inherit_option=True,set_option=True)])
    value['database_acl'] = [dict(recipient='bootstrap-connector',grantor_is_trial_owner=True,
        privilege='CONNECT',grantable=False)] + [dict(recipient='trial-owner',grantor_is_trial_owner=True,
        privilege=k,grantable=False) for k in ('CONNECT','CREATE','TEMPORARY')]
    return value


class FailedSetupCleanupTests(unittest.TestCase):
    def test_exact_reviewed_empty_scope(self):
        cleanup.validate_empty_metadata(metadata())

    def test_wrong_marker_denied(self):
        value=metadata();value['role_marker_matches']=False
        with self.assertRaises(ValueError): cleanup.validate_empty_metadata(value)

    def test_any_relation_denied(self):
        value=metadata();value['permanent_relation_count']=1
        with self.assertRaises(ValueError): cleanup.validate_empty_metadata(value)

    def test_any_function_denied(self):
        value=metadata();value['task_function_count']=1
        with self.assertRaises(ValueError): cleanup.validate_empty_metadata(value)

    def test_login_or_outgoing_membership_denied(self):
        for key in ('login','membership'):
            value=metadata()
            if key=='login': value['role_attributes']['rolcanlogin']=True
            else: value['outgoing_memberships']=1
            with self.subTest(key=key),self.assertRaises(ValueError): cleanup.validate_empty_metadata(value)

    def test_changed_admin_grant_denied(self):
        value=metadata();value['memberships'][1]['admin_option']=True
        with self.assertRaises(ValueError): cleanup.validate_empty_metadata(value)

    def test_public_database_grant_denied(self):
        value=metadata();value['database_acl'].append(dict(recipient='PUBLIC',privilege='CONNECT'))
        with self.assertRaises(ValueError): cleanup.validate_empty_metadata(value)

    def test_fresh_metadata_comparison_precedes_exact_drop(self):
        _,source=cleanup.diagnostic.template(job())
        wanted,record=cleanup.template(job(),dict(record=metadata(),source=source))
        encoded=wanted['containers'][0]['args'][1].split("base64.b64decode('",1)[1].split("'",1)[0]
        code=gzip.decompress(base64.b64decode(encoded)).decode()
        compile(code,'reviewed-empty-cleanup','exec')
        self.assertLess(code.index('if result!='),code.index("raw.execute('SET default_transaction_read_only=off')"))
        self.assertIn("scope['sql'].SQL('DROP DATABASE {}')",code)
        self.assertIn('Active disposable connection; cleanup stopped',code)
        self.assertIn("nspname !~ '^pg_'",code)
        self.assertIn('Disposable relations or sequences exist; cleanup stopped',code)
        self.assertIn('Disposable user functions exist; cleanup stopped',code)
        self.assertNotIn('WITH (FORCE)',code)
        self.assertNotIn('pg_terminate_backend',code)
        self.assertEqual(len(record['cleanup_payload_sha256']),64)


if __name__ == '__main__':
    unittest.main()
