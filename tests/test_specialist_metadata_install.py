import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('metadata_install',ROOT/'scripts/install_specialist_metadata.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.MIGRATIONS={name:(ROOT/'migrations/postgres'/name).read_text(encoding='utf-8') for name in module.EXPECTED}


class MetadataInstallTests(unittest.TestCase):
    def state(self):
        return dict(versions={n:hashlib.sha256(s.encode()).hexdigest() for n,s in module.MIGRATIONS.items()},
            tables=sorted(module.TABLES),functions=sorted(module.FUNCTIONS),roles=[dict(rolname=role,
            **{k:False for k in ('rolcanlogin','rolsuper','rolcreatedb','rolcreaterole','rolbypassrls','rolinherit')})
            for role in module.ROLES.values()])

    def connection(self,memberships=None,rls=3):
        results=iter([memberships or [],rls])
        def execute(*args):
            n=next(results)
            return SimpleNamespace(fetchone=lambda:{'n':n},fetchall=lambda:n)
        return SimpleNamespace(execute=execute)

    def test_exact_dormant_installation_accepted(self):
        result=module.verify(self.connection(),self.state())
        self.assertEqual(result['executable_role_memberships'],0)
        self.assertEqual(result['forced_rls_tables'],3)

    def test_partial_or_changed_migrations_rejected(self):
        for field in ('versions','tables','functions','roles'):
            state=self.state(); state[field]={} if field=='versions' else []
            with self.assertRaises(ValueError): module.verify(self.connection(),state)
        state=self.state(); state['versions'][module.EXPECTED[0]]='changed'
        with self.assertRaises(ValueError): module.verify(self.connection(),state)

    def test_privileged_or_login_roles_rejected(self):
        for key in ('rolcanlogin','rolsuper','rolcreatedb','rolcreaterole','rolbypassrls','rolinherit'):
            state=self.state(); state['roles'][0][key]=True
            with self.assertRaises(ValueError): module.verify(self.connection(),state)

    def test_memberships_or_missing_rls_rejected(self):
        unsafe=dict(is_installing_administrator=False,is_capability_role=True,admin_option=True,inherit_option=False,set_option=False)
        for connection in (self.connection(memberships=[unsafe]),self.connection(rls=2)):
            with self.assertRaises(ValueError): module.verify(connection,self.state())

    def test_only_creator_admin_link_allowed_never_set_or_inherit(self):
        link=dict(is_installing_administrator=True,is_capability_role=True,admin_option=True,inherit_option=False,set_option=False)
        self.assertEqual(module.verify(self.connection([link]),self.state())['creator_admin_only_memberships'],1)
        for key,value in [('is_installing_administrator',False),('is_capability_role',False),('admin_option',False),('inherit_option',True),('set_option',True)]:
            with self.assertRaises(ValueError): module.verify(self.connection([dict(link,**{key:value})]),self.state())
