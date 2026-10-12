import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('authority_install',ROOT/'scripts/install_specialist_authority.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.MIGRATIONS={name:(ROOT/'migrations/postgres'/name).read_text(encoding='utf-8') for name in module.EXPECTED}


class AuthorityInstallTests(unittest.TestCase):
    def state(self):
        return dict(versions={n:hashlib.sha256(s.encode()).hexdigest() for n,s in module.MIGRATIONS.items()},
            tables=[dict(relname=n,relkind='r',relrowsecurity=True,relforcerowsecurity=True) for n in module.TABLES],
            roles=[dict(rolname=module.ROLE,**{k:False for k in module.ROLE_FLAGS})])

    def connection(self,**changes):
        values=dict(memberships=[],exposed={'n':0},first={'read':True,'forbidden':False},second={'read':True,'forbidden':False},
            privileged={'n':0},schemas={'n':0},database={'allowed':False},access={'usage':True,'tenant_function':True},
            policies=[dict(tablename=n,policyname='tenant_isolation',permissive='PERMISSIVE',roles=['public'],
                cmd='ALL',qual='(tenant_id = app_tenant_id())',with_check='(tenant_id = app_tenant_id())') for n in module.TABLES],
            visible={'controls':0,'grants':0})
        values.update(changes)
        results=iter(values.values())
        def execute(*args):
            result=next(results)
            return SimpleNamespace(fetchone=lambda:result,fetchall=lambda:result)
        return SimpleNamespace(execute=execute)

    def test_exact_dormant_install_accepted(self):
        result=module.verify(self.connection(),self.state())
        self.assertTrue(result['capability_select_only'])
        self.assertEqual(result['forced_rls_tables'],2)
        self.assertFalse(result['unrelated_table_access'])

    def test_partial_or_changed_installation_rejected(self):
        for key in ('versions','tables','roles'):
            state=self.state();state[key]={} if key=='versions' else []
            with self.assertRaises(ValueError): module.verify(self.connection(),state)
        state=self.state();state['versions'][module.EXPECTED[0]]='changed'
        with self.assertRaises(ValueError): module.verify(self.connection(),state)

    def test_every_elevated_role_flag_rejected(self):
        for flag in module.ROLE_FLAGS:
            state=self.state();state['roles'][0][flag]=True
            with self.assertRaises(ValueError): module.verify(self.connection(),state)

    def test_creator_admin_only_no_inherit_or_set(self):
        link=dict(is_installing_administrator=True,is_capability_role=True,admin_option=True,inherit_option=False,set_option=False)
        self.assertEqual(module.verify(self.connection(memberships=[link]),self.state())['creator_admin_only_memberships'],1)
        for key,value in [('is_installing_administrator',False),('is_capability_role',False),('admin_option',False),('inherit_option',True),('set_option',True)]:
            with self.assertRaises(ValueError): module.verify(self.connection(memberships=[dict(link,**{key:value})]),self.state())

    def test_unrelated_access_or_mutation_rejected(self):
        for change in [dict(exposed={'n':1}),dict(first={'read':True,'forbidden':True}),
            dict(second={'read':False,'forbidden':False}),dict(privileged={'n':1}),dict(schemas={'n':1}),
            dict(database={'allowed':True}),dict(access={'usage':False,'tenant_function':True})]:
            with self.assertRaises(ValueError): module.verify(self.connection(**change),self.state())

    def test_policy_or_existing_enrollment_rejected(self):
        for change in [dict(policies=[]),dict(visible={'controls':1,'grants':0}),dict(visible={'controls':0,'grants':1})]:
            with self.assertRaises(ValueError): module.verify(self.connection(**change),self.state())
        for flag in ('relrowsecurity','relforcerowsecurity'):
            state=self.state();state['tables'][0][flag]=False
            with self.assertRaises(ValueError): module.verify(self.connection(),state)

    def test_installer_is_narrow_and_has_committed_readback(self):
        text=(ROOT/'scripts/install_specialist_authority.py').read_text()
        self.assertEqual(module.EXPECTED,('058_specialist_observation_authority.sql',))
        self.assertIn("result['committed_readback']=verify(c,inspect(c))",text)
        self.assertNotIn('CREATE LOGIN',text)
        self.assertNotIn('INSERT INTO public.specialist_observation',text)
        self.assertNotIn('GRANT ibkr_specialist_authority_reader TO',text)

    def main_connection(self):
        c=MagicMock();c.__enter__.return_value=c
        c.info.dbname='ibkr_staging'
        c.info.host='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
        return c

    def test_preflight_does_not_write(self):
        c=self.main_connection()
        with patch.object(module,'INSTALL_APPLY',False,create=True), \
            patch.dict(module.os.environ,{'DATABASE_URL':'synthetic'}), \
            patch.object(module.psycopg,'connect',return_value=c), \
            patch.object(module,'inspect',return_value={'versions':{},'tables':[],'roles':[]}), \
            patch('builtins.print') as output:
            module.main()
        self.assertIn('ready_for_additive_install',output.call_args.args[0])
        self.assertTrue(all(call.args[0].startswith(('SELECT','SET')) for call in c.execute.call_args_list))

    def test_failed_in_transaction_validation_rolls_back(self):
        c=self.main_connection()
        with patch.object(module,'INSTALL_APPLY',True,create=True), \
            patch.dict(module.os.environ,{'DATABASE_URL':'synthetic'}), \
            patch.object(module.psycopg,'connect',return_value=c), \
            patch.object(module,'inspect',side_effect=[{'versions':{},'tables':[],'roles':[]},self.state()]), \
            patch.object(module,'verify',side_effect=ValueError('unsafe permission')):
            with self.assertRaises(ValueError): module.main()
        self.assertEqual(c.transaction.call_count,1)
        self.assertIs(c.transaction.return_value.__exit__.call_args.args[0],ValueError)

    def test_success_is_verified_again_after_commit(self):
        c=self.main_connection()
        with patch.object(module,'INSTALL_APPLY',True,create=True), \
            patch.dict(module.os.environ,{'DATABASE_URL':'synthetic'}), \
            patch.object(module.psycopg,'connect',return_value=c), \
            patch.object(module,'inspect',side_effect=[{'versions':{},'tables':[],'roles':[]},self.state(),self.state()]), \
            patch.object(module,'verify',return_value={'capability_select_only':True}) as verifier, \
            patch('builtins.print') as output:
            module.main()
        self.assertEqual(verifier.call_count,2)
        self.assertEqual(c.transaction.call_count,2)
        self.assertIn('committed_readback',output.call_args.args[0])

    def test_existing_install_is_read_only_and_unknown_database_rejected(self):
        c=self.main_connection()
        with patch.object(module,'INSTALL_APPLY',True,create=True), \
            patch.dict(module.os.environ,{'DATABASE_URL':'synthetic'}), \
            patch.object(module.psycopg,'connect',return_value=c), \
            patch.object(module,'inspect',return_value=self.state()), \
            patch.object(module,'verify',return_value={'capability_select_only':True}), \
            patch('builtins.print') as output:
            module.main()
            self.assertIn('already_installed',output.call_args.args[0])
            self.assertTrue(all(call.args[0].startswith(('SELECT','SET')) for call in c.execute.call_args_list))
            c.info.dbname='other'
            with self.assertRaises(ValueError): module.main()
