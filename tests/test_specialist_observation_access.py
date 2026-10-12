import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from ibkr_etl import specialist_observation_access as access


def rows():
    tables=sorted(set(access.COLUMNS)-{'subject_nav_series'})
    policies=[]
    for table in tables:
        expr='account_belongs_to_current_tenant(account_id)' if table=='nav_snapshots' else '(tenant_id = app_tenant_id())'
        name=table+'_tenant_policy' if table in ('platform_subjects','subject_feature_states','app_user_roles') else 'account_isolation' if table=='nav_snapshots' else 'tenant_isolation'
        policies.append(dict(tablename=table,policyname=name,permissive='PERMISSIVE',roles=['public'],cmd='ALL',qual=expr,with_check=expr))
    return [dict(rolcanlogin=False,rolinherit=False,rolsuper=False,rolcreatedb=False,rolcreaterole=False,rolreplication=False,rolbypassrls=False),
        {'n':0},[{'schema':'public','table':t,'column':col,'readable':True,'writable':False} for t,col in access.expected_columns()],
        {'n':0},{'n':0},{'n':0},{'allowed':False},
        [dict(relname=t,relrowsecurity=True,relforcerowsecurity=True) for t in tables],policies,
        dict(reloptions=['security_invoker=true'],definition='synthetic view definition')]


def connection(data):
    c=Mock();iterator=iter(data)
    def execute(*args):
        value=next(iterator)
        return SimpleNamespace(fetchone=lambda:value,fetchall=lambda:value)
    c.execute.side_effect=execute
    return c


class ObservationAccessTests(unittest.TestCase):
    def test_exact_column_grants_only(self):
        for role in (access.ROLE,access.TRIAL_ROLE):
            grants=access.grant_statements(role)
            self.assertEqual(len(grants),10)
            self.assertTrue(all(x.startswith('GRANT SELECT (') for x in grants[1:]))
            self.assertFalse(any('raw_reports' in x or 'credentials' in x for x in grants))
        for role in ('public','owner','reader; DROP TABLE accounts'):
            with self.assertRaises(ValueError): access.grant_statements(role)

    def test_permission_verifier_is_read_only(self):
        c=connection(rows());result=access.verify_permissions(c,access.TRIAL_ROLE)
        self.assertEqual(result['column_permissions_verified'],len(access.expected_columns()))
        self.assertEqual(result['write_privileges'],0)
        self.assertEqual(result['forced_rls_tables'],8)
        self.assertTrue(all(call.args[0].lstrip().startswith('SELECT') for call in c.execute.call_args_list))

    def test_elevated_attributes_and_memberships_rejected(self):
        for field in rows()[0]:
            data=rows();data[0][field]=True
            with self.assertRaises(ValueError): access.verify_permissions(connection(data),access.ROLE)
        data=rows();data[1]={'n':1}
        with self.assertRaises(ValueError): access.verify_permissions(connection(data),access.ROLE)

    def test_missing_extra_and_writable_columns_rejected(self):
        for change in ('missing','extra','writable'):
            data=rows()
            if change=='missing': data[2].pop()
            if change=='extra': data[2].append({'schema':'public','table':'raw_reports','column':'file_path','readable':True,'writable':False})
            if change=='writable': data[2][0]['writable']=True
            with self.assertRaises(ValueError): access.verify_permissions(connection(data),access.ROLE)

    def test_broad_table_function_and_create_privileges_rejected(self):
        for index in (3,4,5,6):
            data=rows();data[index]={'allowed':True} if index==6 else {'n':1}
            with self.assertRaises(ValueError): access.verify_permissions(connection(data),access.ROLE)

    def test_rls_view_and_policy_drift_rejected(self):
        for index in (7,8,9):
            data=rows();data[index]=[] if index!=9 else dict(reloptions=[],definition='view')
            with self.assertRaises(ValueError): access.verify_permissions(connection(data),access.ROLE)
        data=rows();data[8][0]['qual']='true'
        with self.assertRaises(ValueError): access.verify_permissions(connection(data),access.ROLE)

    def test_trial_contains_rollback_and_no_financial_fetch(self):
        source=(Path(__file__).resolve().parents[1]/'scripts/verify_specialist_observation_reader.py').read_text()
        compile(source,'trial','exec')
        self.assertIn('force_rollback=True',source)
        self.assertIn('LIMIT 0',source)
        self.assertNotIn('UPDATE ',source);self.assertNotIn('INSERT INTO',source)
        self.assertNotIn('DELETE FROM',source);self.assertNotIn('CREATE POLICY',source)

    def test_trial_launcher_does_not_accept_apply(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import run_specialist_audit_trial as launcher
        with patch.object(sys,'argv',['trial','start','--kind','observation_reader','--apply']),patch.object(launcher,'az') as az:
            with self.assertRaises(ValueError): launcher.main()
            az.assert_not_called()

    def test_archive_rejects_missing_failed_or_mutating_trial_receipt(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
        import archive_specialist_acceptance as archive
        record=dict(status='passed',column_permissions_verified=41,write_privileges=0,
            raw_statement_access=False,credential_access=False,forced_rls_tables=8,
            allowed_relation_checks=9,denied_column_checks=4,financial_rows_read=0,
            source_rows_written=0,trial_role_rolled_back=True,service_login_created=False,agents_enabled=0,
            nav_view_sha256='a'*64)
        archive.verify_observation_reader_receipt(record)
        for key in record:
            bad=dict(record);bad.pop(key)
            with self.assertRaises(ValueError): archive.verify_observation_reader_receipt(bad)
        for key,value in [('source_rows_written',1),('trial_role_rolled_back',False),('write_privileges',False),('agents_enabled',1)]:
            with self.assertRaises(ValueError): archive.verify_observation_reader_receipt(dict(record,**{key:value}))
