import base64
import gzip
import copy
from contextlib import contextmanager
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,MagicMock,patch

from ibkr_etl import specialist_authority_runtime as runtime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import deploy_specialist_authority_worker as deployment
import provision_specialist_authority_login as provisioner
import run_specialist_authority_worker as worker


def connection(changes=None):
    rows=[dict(role=runtime.ROLE,login=runtime.LOGIN,tenant=runtime.TENANT,read_only='on',isolation='read committed'),
        [dict(rolname=n,rolcanlogin=n==runtime.LOGIN,rolinherit=False,rolsuper=False,rolcreatedb=False,
            rolcreaterole=False,rolreplication=False,rolbypassrls=False) for n in (runtime.LOGIN,runtime.ROLE)],
        [{'rolname':runtime.LOGIN},{'rolname':runtime.ROLE}],
        [dict(role=runtime.ROLE,member=runtime.LOGIN,admin_option=False,inherit_option=False,set_option=True)]]
    for principal in (runtime.LOGIN,runtime.ROLE):
        rows.extend([{'n':0},{'n':0},{'n':0},{'allowed':False},
            dict(read=principal==runtime.ROLE,forbidden=False),dict(read=principal==runtime.ROLE,forbidden=False)])
    rows.extend([[dict(relname=n,relrowsecurity=True,relforcerowsecurity=True) for n in runtime.TABLES],
        [dict(tablename=n,policyname='tenant_isolation',permissive='PERMISSIVE',roles=['public'],cmd='ALL',
            qual='(tenant_id = app_tenant_id())',with_check='(tenant_id = app_tenant_id())') for n in runtime.TABLES]])
    for i,value in (changes or {}).items(): rows[i]=value
    c=Mock();results=iter(rows)
    def execute(*args):
        value=next(results)
        return SimpleNamespace(fetchone=lambda:value,fetchall=lambda:value)
    c.execute.side_effect=execute
    return c


class AuthorityRuntimeTests(unittest.TestCase):
    def test_worker_checks_real_authority_source_without_enrollment(self):
        c=Mock();c.execute.return_value.fetchone.return_value={'controls':0,'grants':0}
        @contextmanager
        def factory(value,tenant):
            self.assertEqual(tenant,runtime.TENANT)
            yield c
        source=Mock();source.load_current.return_value=SimpleNamespace(grant=None,kill_switch=True)
        with patch.dict(worker.os.environ,{'SPECIALIST_AUTHORITY_DATABASE_URL':'synthetic'}), \
            patch.object(worker,'authority_database',side_effect=factory), \
            patch.object(worker,'PostgresAuthoritySource',return_value=source),patch('builtins.print') as output:
            worker.main()
            self.assertIn('"unregistered_principal_denied": true',output.call_args.args[0])
            source.load_current.assert_called_once()
            source.load_current.return_value=SimpleNamespace(grant=object(),kill_switch=False)
            with self.assertRaises(ValueError): worker.main()
            # Existing enrollment is not a failed permission check. The
            # synthetic unregistered principal must still be denied.
            source.load_current.return_value=SimpleNamespace(grant=None,kill_switch=True)
            for counts in ({'controls':1,'grants':0},{'controls':1,'grants':6}):
                c.execute.return_value.fetchone.return_value=counts
                worker.main()
                self.assertIn('"unregistered_principal_denied": true',output.call_args.args[0])
            for counts in ({'controls':0,'grants':1},{'controls':2,'grants':0},{'controls':1,'grants':-1},{'controls':True,'grants':0}):
                c.execute.return_value.fetchone.return_value=counts
                with self.subTest(counts=counts),self.assertRaises(ValueError):worker.main()
            c.execute.return_value.fetchone.return_value={'controls':1,'grants':6}
            source.load_current.return_value=SimpleNamespace(grant=None,kill_switch=False)
            with self.assertRaises(ValueError):worker.main()

    def test_exact_reader_passes_without_writes(self):
        c=connection();result=runtime.verify_authority_access(c)
        self.assertFalse(result['source_table_access']);self.assertFalse(result['authority_mutation_allowed'])
        self.assertTrue(all(call.args[0].lstrip().startswith('SELECT') for call in c.execute.call_args_list))

    def test_wrong_identity_or_writable_transaction_rejected(self):
        valid=dict(role=runtime.ROLE,login=runtime.LOGIN,tenant=runtime.TENANT,read_only='on',isolation='read committed')
        for key,value in [('role','admin'),('login','admin'),('tenant','other'),('read_only','off'),('isolation','repeatable read')]:
            with self.assertRaises(ValueError): runtime.verify_authority_access(connection({0:dict(valid,**{key:value})}))

    def test_role_expansion_rejected(self):
        for index,value in [(2,[{'rolname':runtime.LOGIN},{'rolname':runtime.ROLE},{'rolname':'admin'}]),
            (3,[dict(role=runtime.ROLE,member=runtime.LOGIN,admin_option=True,inherit_option=False,set_option=True)])]:
            with self.assertRaises(ValueError): runtime.verify_authority_access(connection({index:value}))

    def test_direct_and_capability_privileges_checked(self):
        for index in (4,5,6,10,11,12):
            with self.subTest(index=index),self.assertRaises(ValueError): runtime.verify_authority_access(connection({index:{'n':1}}))
        for index in (7,13):
            with self.assertRaises(ValueError): runtime.verify_authority_access(connection({index:{'allowed':True}}))
        for index in (8,9,14,15):
            with self.assertRaises(ValueError): runtime.verify_authority_access(connection({index:dict(read=True,forbidden=True)}))
        for index in (16,17):
            with self.assertRaises(ValueError): runtime.verify_authority_access(connection({index:[]}))

    def test_exact_connection_and_tenant_required(self):
        value=f'postgresql://{runtime.LOGIN}:synthetic@{runtime.HOST}:5432/{runtime.DATABASE}?sslmode=require'
        runtime.validate_database_url(value)
        for other in (value.replace(runtime.LOGIN,'admin'),value.replace(runtime.HOST,'other'),value.replace('require','disable'),value+'&options=',value+'#other'):
            with self.assertRaises(ValueError): runtime.validate_database_url(other)
        with patch('psycopg.connect') as connect:
            with self.assertRaises(ValueError):
                with runtime.authority_database(value,'other'): pass
            connect.assert_not_called()

    def test_admin_set_role_not_accepted_as_service_login(self):
        c=Mock();c.execute.return_value.fetchone.return_value={'login':'admin'}
        with self.assertRaises(ValueError): runtime.bind_authority_session(c)
        self.assertEqual(c.execute.call_count,2)

    def test_factory_uses_fresh_transaction_and_verifies_every_time(self):
        value=f'postgresql://{runtime.LOGIN}:synthetic@{runtime.HOST}:5432/{runtime.DATABASE}?sslmode=require'
        raw=MagicMock();raw.__enter__.return_value=raw
        with patch('psycopg.connect',return_value=raw) as connect,patch.object(runtime,'bind_authority_session') as bind,patch.object(runtime,'verify_authority_access') as verify:
            for _ in range(2):
                with runtime.authority_database(value,runtime.TENANT): pass
            self.assertEqual(connect.call_count,2);self.assertEqual(bind.call_count,2);self.assertEqual(verify.call_count,2)


class AuthorityDeploymentTests(unittest.TestCase):
    def test_cloud_role_scope_expansion_rejected(self):
        identity=dict(tags={'purpose':deployment.PURPOSE},properties=dict(principalId='00000000-0000-0000-0000-000000000004',clientId='client'))
        grants=[dict(scope=deployment.BASE+'Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo',role='roles/7f951dda-4ed3-4680-a7ca-43fe172d538d'),
            dict(scope=deployment.BASE+f'Microsoft.KeyVault/vaults/{deployment.VAULT}/secrets/{deployment.SECRET}',role='roles/4633458b-17de-408a-b874-0445c86b69e6')]
        with patch.object(deployment,'request',return_value=identity),patch.object(deployment,'az',return_value=grants) as read:
            self.assertEqual(deployment.verify_cloud_identity()['registered_rbac_assignments'],2)
            read.return_value=grants+[dict(scope='/subscriptions/other',role='roles/owner')]
            with self.assertRaises(ValueError): deployment.verify_cloud_identity()
            read.return_value=[grants[0],dict(grants[1],scope=deployment.BASE)]
            with self.assertRaises(ValueError): deployment.verify_cloud_identity()

    def test_manual_isolated_worker(self):
        resource=deployment.definition();deployment.validate(resource)
        self.assertEqual(resource['properties']['configuration']['triggerType'],'Manual')
        self.assertEqual(resource['properties']['configuration']['replicaRetryLimit'],0)
        self.assertNotIn('database-admin-url',str(resource));self.assertNotIn('ingestion-identity',str(resource))
        self.assertEqual(len(resource['properties']['configuration']['secrets']),1)
        for name in (deployment.JOB,deployment.PROVISION): self.assertLessEqual(len(name),32)

    def test_bundles_compile_and_fit_argument_limit(self):
        for provision in (False,True):
            args,digest=deployment.bundle(provision)
            source=gzip.decompress(base64.b64decode(args[1].split("b64decode('")[1].split("')")[0])).decode()
            compile(source,'authority','exec')
            self.assertLess(len(args[1]),30000);self.assertEqual(len(digest),64)
            if not provision:
                self.assertIn('PostgresAuthoritySource(factory)',source)
                self.assertNotIn('CREATE ROLE',source)

    def test_job_drift_rejected(self):
        for key,value in [('triggerType','Schedule'),('replicaRetryLimit',1)]:
            resource=deployment.definition();resource['properties']['configuration'][key]=value
            with self.assertRaises(ValueError): deployment.validate(resource)
        resource=deployment.definition();resource['identity']['type']='SystemAssigned, UserAssigned'
        with self.assertRaises(ValueError): deployment.validate(resource)
        resource=deployment.definition();resource['properties']['template']['initContainers']=[{'name':'other'}]
        with self.assertRaises(ValueError): deployment.validate(resource)

    def test_failed_new_login_quarantined(self):
        admin=MagicMock();admin.__enter__.return_value=admin;admin.info.host=runtime.HOST;admin.info.dbname=runtime.DATABASE
        admin.execute.return_value.fetchone.return_value=None
        value=f'postgresql://{runtime.LOGIN}:synthetic@{runtime.HOST}:5432/{runtime.DATABASE}?sslmode=require'
        with patch.dict(provisioner.os.environ,{'DATABASE_URL':'admin-test','SPECIALIST_AUTHORITY_DATABASE_URL':value}), \
            patch.object(provisioner.psycopg,'connect',side_effect=[admin,RuntimeError('login failed')]):
            with self.assertRaises(RuntimeError): provisioner.main()
        self.assertEqual(admin.execute.call_args.args[0],'ALTER ROLE ibkr_specialist_authority_login NOLOGIN')

    def test_existing_login_not_changed(self):
        admin=MagicMock();admin.__enter__.return_value=admin;admin.info.host=runtime.HOST;admin.info.dbname=runtime.DATABASE
        admin.execute.return_value.fetchone.return_value={'exists':True}
        value=f'postgresql://{runtime.LOGIN}:synthetic@{runtime.HOST}:5432/{runtime.DATABASE}?sslmode=require'
        with patch.dict(provisioner.os.environ,{'DATABASE_URL':'admin-test','SPECIALIST_AUTHORITY_DATABASE_URL':value}), \
            patch.object(provisioner.psycopg,'connect',return_value=admin):
            with self.assertRaises(ValueError): provisioner.main()
        self.assertEqual(admin.execute.call_count,1)
