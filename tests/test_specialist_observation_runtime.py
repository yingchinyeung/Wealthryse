import base64
import gzip
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock,MagicMock,patch
from ibkr_etl import specialist_observation_runtime as runtime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import deploy_specialist_observation_worker as deployment
import provision_specialist_observation_login as provisioner


def rows():
    return [dict(role=runtime.ROLE,login=runtime.LOGIN,tenant=runtime.TENANT,read_only='on',isolation='read committed'),
        dict(rolcanlogin=True,rolinherit=False,rolsuper=False,rolcreatedb=False,rolcreaterole=False,
            rolreplication=False,rolbypassrls=False,rolconnlimit=2),
        [dict(rolname=n) for n in (runtime.LOGIN,runtime.ROLE)],
        [dict(role=runtime.ROLE,admin_option=False,inherit_option=False,set_option=True)],
        {'n':0},{'n':0},{'n':0},{'allowed':False}]


def connection(values):
    c=Mock();iterator=iter(values)
    def execute(*args):
        value=next(iterator)
        return SimpleNamespace(fetchone=lambda:value,fetchall=lambda:value)
    c.execute.side_effect=execute
    return c


class ObservationRuntimeTests(TestCase):
    def setUp(self):
        self.evidence=dict(nav_view_sha256=runtime.NAV_VIEW_SHA256,column_permissions_verified=41,write_privileges=0)
        self.value=f'postgresql://{runtime.LOGIN}:synthetic@{runtime.HOST}:5432/{runtime.DATABASE}?sslmode=require'

    def test_exact_login_and_capability_checked(self):
        with patch.object(runtime,'verify_permissions',return_value=self.evidence) as verify:
            c=connection(rows());result=runtime.verify_observation_access(c)
            self.assertTrue(result['reachable_roles_verified']);self.assertFalse(result['source_mutation_authority'])
            verify.assert_called_once_with(c,runtime.ROLE)
            self.assertTrue(all(call.args[0].lstrip().startswith('SELECT') for call in c.execute.call_args_list))

    def test_connection_tenant_and_ssl_bound(self):
        runtime.validate_database_url(self.value)
        for value in (self.value.replace(runtime.LOGIN,'admin'),self.value.replace('require','disable'),self.value+'&options=x',self.value+'#x'):
            with self.assertRaises(ValueError): runtime.validate_database_url(value)
        with patch('psycopg.connect') as connect:
            with self.assertRaises(ValueError):
                with runtime.observation_database(self.value,'other'): pass
            connect.assert_not_called()

    def test_admin_set_role_cannot_pass_as_service_login(self):
        c=Mock();c.execute.return_value.fetchone.return_value={'login':'admin'}
        with self.assertRaises(ValueError): runtime.bind_observation_session(c)
        self.assertEqual(c.execute.call_count,2)

    def test_identity_membership_and_direct_permissions_rejected(self):
        changes=[(0,dict(rows()[0],login='admin')),(1,dict(rows()[1],rolsuper=True)),
            (2,rows()[2]+[dict(rolname='other')]),(3,[dict(rows()[3][0],admin_option=True)]),
            (4,{'n':1}),(5,{'n':1}),(6,{'n':1}),(7,{'allowed':True})]
        for index,value in changes:
            data=rows();data[index]=value
            with self.subTest(index=index),self.assertRaises(ValueError): runtime.verify_observation_access(connection(data))

    def test_changed_nav_view_blocks_read(self):
        with patch.object(runtime,'verify_permissions',return_value=dict(self.evidence,nav_view_sha256='f'*64)):
            with self.assertRaises(ValueError): runtime.verify_observation_access(connection(rows()))

    def test_fresh_guarded_connection_every_time(self):
        raw=MagicMock();raw.__enter__.return_value=raw
        with patch('psycopg.connect',return_value=raw) as connect,patch.object(runtime,'bind_observation_session') as bind,patch.object(runtime,'verify_observation_access') as verify:
            for _ in range(2):
                with runtime.observation_database(self.value,runtime.TENANT): pass
            self.assertEqual(connect.call_count,2);self.assertEqual(bind.call_count,2);self.assertEqual(verify.call_count,2)

    def test_failed_created_login_quarantined(self):
        admin=MagicMock();admin.__enter__.return_value=admin;admin.info.host=runtime.HOST;admin.info.dbname=runtime.DATABASE
        admin.execute.return_value.fetchone.return_value=None
        with patch.dict(provisioner.os.environ,{'DATABASE_URL':'synthetic-admin','SPECIALIST_OBSERVATION_DATABASE_URL':self.value}), \
            patch.object(provisioner.psycopg,'connect',side_effect=[admin,RuntimeError('synthetic failure')]), \
            patch.object(provisioner,'verify_permissions'),patch.object(provisioner,'verify_creator_membership'):
            with self.assertRaises(RuntimeError): provisioner.main()
            self.assertIn('ALTER ROLE ibkr_specialist_observation_login NOLOGIN',[c.args[0] for c in admin.execute.call_args_list if isinstance(c.args[0],str)])

    def test_existing_roles_never_altered(self):
        admin=MagicMock();admin.__enter__.return_value=admin;admin.info.host=runtime.HOST;admin.info.dbname=runtime.DATABASE
        admin.execute.return_value.fetchone.return_value={'exists':1}
        with patch.dict(provisioner.os.environ,{'DATABASE_URL':'synthetic-admin','SPECIALIST_OBSERVATION_DATABASE_URL':self.value}),patch.object(provisioner.psycopg,'connect',return_value=admin):
            with self.assertRaises(ValueError): provisioner.main()
            self.assertEqual(admin.execute.call_count,1)

    def test_creator_link_verified_without_self_grant(self):
        c=Mock();c.execute.return_value.fetchall.return_value=[dict(admin_option=True,inherit_option=False,set_option=False)]
        provisioner.verify_creator_membership(c,runtime.ROLE)
        self.assertTrue(c.execute.call_args.args[0].lstrip().startswith('SELECT'))
        for bad in ([],[dict(admin_option=True,inherit_option=True,set_option=False)],[dict(admin_option=True,inherit_option=False,set_option=True)]):
            c.execute.return_value.fetchall.return_value=bad
            with self.assertRaises(ValueError): provisioner.verify_creator_membership(c,runtime.ROLE)


class ObservationDeploymentTests(TestCase):
    def test_isolated_manual_worker_and_secret_binding(self):
        resource=deployment.definition();deployment.validate(resource)
        self.assertEqual(resource['properties']['configuration']['triggerType'],'Manual')
        self.assertEqual(resource['properties']['configuration']['replicaRetryLimit'],0)
        self.assertEqual(resource['properties']['template']['containers'][0]['env'],[dict(name='SPECIALIST_OBSERVATION_DATABASE_URL',secretRef='observation-url')])
        self.assertNotIn('database-admin-url',str(resource));self.assertNotIn('ingestion-identity',str(resource))

    def test_bundles_compile_and_are_bounded(self):
        for provision in (False,True):
            args,digest=deployment.bundle(provision)
            source=gzip.decompress(base64.b64decode(args[1].split("b64decode('")[1].split("')")[0])).decode()
            compile(source,'observation-worker','exec')
            self.assertLess(len(args[1]),30000);self.assertEqual(len(digest),64)
            if not provision:
                self.assertIn('LIMIT 0',source);self.assertNotIn('CREATE ROLE',source)

    def test_job_drift_rejected(self):
        for key,value in [('triggerType','Schedule'),('replicaRetryLimit',1)]:
            resource=deployment.definition();resource['properties']['configuration'][key]=value
            with self.assertRaises(ValueError): deployment.validate(resource)
        for name in (deployment.JOB,deployment.PROVISION): self.assertLessEqual(len(name),32)
        with self.assertRaises(ValueError): deployment.job_path('ibkrv2stg-web')

    def test_exact_cloud_identity_scopes(self):
        identity=dict(tags={'purpose':deployment.PURPOSE},properties=dict(principalId='00000000-0000-0000-0000-000000000004',clientId='client'))
        grants=[dict(scope=deployment.BASE+'Microsoft.ContainerRegistry/registries/ibkrv2stgauhecwc4itaoo',role='roles/7f951dda-4ed3-4680-a7ca-43fe172d538d'),
            dict(scope=deployment.BASE+f'Microsoft.KeyVault/vaults/{deployment.VAULT}/secrets/{deployment.SECRET}',role='roles/4633458b-17de-408a-b874-0445c86b69e6')]
        with patch.object(deployment,'request',return_value=identity),patch.object(deployment,'az',return_value=grants) as az:
            self.assertEqual(deployment.verify_cloud_identity()['registered_rbac_assignments'],2)
            az.return_value=grants+[dict(scope=deployment.BASE,role='roles/owner')]
            with self.assertRaises(ValueError): deployment.verify_cloud_identity()
