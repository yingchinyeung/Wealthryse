import base64
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch
from ibkr_etl import specialist_report_runtime as runtime

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import deploy_specialist_report_worker as deployment
import provision_specialist_report_login as provisioner


class AccessConnection:
    def __init__(self,changes=None):
        rows=[dict(role=runtime.ROLE,login=runtime.LOGIN,tenant=runtime.TENANT),
              [dict(rolname=name,rolsuper=False,rolcreatedb=False,rolcreaterole=False,rolreplication=False,rolbypassrls=False)
               for name in (runtime.LOGIN,runtime.ROLE)],{'n':0},{'n':0},{'allowed':False},{'n':3},
              {'read':True,'insert':False,'broad_write':False},
              {'read':True,'insert':True,'broad_write':False},{'read':True,'insert':True,'broad_write':False}]
        for index,value in (changes or {}).items(): rows[index]=value
        self.rows=iter(rows)
    def execute(self,*args):
        row=next(self.rows)
        return SimpleNamespace(fetchone=lambda:row,fetchall=lambda:row)


class ReportRuntimeTests(unittest.TestCase):
    def test_url_rejects_other_identity_destination_and_options(self):
        value=f'postgresql://{runtime.LOGIN}:synthetic@{runtime.HOST}:5432/{runtime.DATABASE}?sslmode=require'
        self.assertEqual(runtime.validate_database_url(value).username,runtime.LOGIN)
        for bad in (value.replace(runtime.LOGIN,'administrator'),value.replace(runtime.HOST,'other.example'),
                    value.replace('require','disable'),value+'&options=bad',value.replace(':5432',':1234'),value+'#secret'):
            with self.assertRaises(ValueError): runtime.validate_database_url(bad)

    def test_access_preflight_is_strict(self):
        self.assertFalse(runtime.verify_report_access(AccessConnection())['source_table_access'])
        for index,value in [(0,{'role':'admin','login':runtime.LOGIN,'tenant':runtime.TENANT}),
                            (1,[]),(2,{'n':1}),(3,{'n':1}),(4,{'allowed':True}),(5,{'n':2}),
                            (6,{'read':True,'insert':True,'broad_write':False}),
                            (8,{'read':True,'insert':True,'broad_write':True})]:
            with self.subTest(index=index),self.assertRaises(ValueError):
                runtime.verify_report_access(AccessConnection({index:value}))

    def test_verify_mode_never_calls_builder_or_publisher(self):
        with patch.object(runtime,'bind_report_session'),patch.object(runtime,'verify_report_access',return_value={}), \
             patch('ibkr_etl.agent_daily_report.prepare_daily_report') as builder, \
             patch('ibkr_etl.agent_daily_inbox.publish_website_report') as publisher:
            result=runtime.run_report(Mock(),None,report_date=None)
            self.assertEqual(result['reports_created'],0)
            builder.assert_not_called(); publisher.assert_not_called()
            with self.assertRaises(ValueError): runtime.run_report(Mock(),None,report_date=None,mode='execute_sql')

    def test_admin_connection_cannot_masquerade_as_report_login(self):
        c=Mock(autocommit=True,info=SimpleNamespace(transaction_status=0))
        c.execute.return_value.fetchone.return_value={'login':'admin'}
        with self.assertRaises(ValueError): runtime.bind_report_session(c)
        self.assertEqual(c.execute.call_count,1)


class ReportDeploymentTests(unittest.TestCase):
    def test_manual_worker_has_one_restricted_secret_and_no_admin_identity(self):
        for name in (deployment.JOB,deployment.PROVISION):
            self.assertLessEqual(len(name),32)
        resource=deployment.definition(); config=resource['properties']['configuration']
        self.assertEqual(config['triggerType'],'Manual'); self.assertEqual(config['replicaRetryLimit'],0)
        self.assertNotIn('scheduleTriggerConfig',config)
        self.assertEqual(len(config['secrets']),1)
        self.assertNotIn('database-admin-url',str(config)); self.assertNotIn('ingestion-identity',str(resource))
        deployment.validate_job(resource)

    def test_failed_new_login_verification_disables_only_new_login(self):
        admin=MagicMock()
        admin.__enter__.return_value=admin
        admin.info.host=runtime.HOST; admin.info.dbname=runtime.DATABASE
        admin.execute.return_value.fetchone.return_value=None
        value=f'postgresql://{runtime.LOGIN}:synthetic@{runtime.HOST}:5432/{runtime.DATABASE}?sslmode=require'
        with patch.dict(provisioner.os.environ,{'DATABASE_URL':'admin-test','SPECIALIST_REPORT_DATABASE_URL':value}), \
             patch.object(provisioner.psycopg,'connect',side_effect=[admin,RuntimeError('synthetic verification failure')]):
            with self.assertRaises(RuntimeError): provisioner.main()
        self.assertEqual(admin.execute.call_args.args[0],'ALTER ROLE ibkr_specialist_report_login NOLOGIN')

    def test_existing_login_is_not_replaced_or_disabled(self):
        admin=MagicMock(); admin.__enter__.return_value=admin
        admin.info.host=runtime.HOST; admin.info.dbname=runtime.DATABASE
        admin.execute.return_value.fetchone.return_value={'exists':1}
        value=f'postgresql://{runtime.LOGIN}:synthetic@{runtime.HOST}:5432/{runtime.DATABASE}?sslmode=require'
        with patch.dict(provisioner.os.environ,{'DATABASE_URL':'admin-test','SPECIALIST_REPORT_DATABASE_URL':value}), \
             patch.object(provisioner.psycopg,'connect',return_value=admin):
            with self.assertRaises(ValueError): provisioner.main()
        self.assertEqual(admin.execute.call_count,1)

    def test_payloads_compile_and_no_build_entrypoint_deployed(self):
        for provision in (False,True):
            args,digest=deployment.bundle(provision)
            source=base64.b64decode(args[1].split("b64decode('")[1].split("')")[0]).decode()
            compile(source,'worker','exec')
            self.assertEqual(len(digest),64)
            if not provision:
                self.assertIn("mode='verify'",source)
                self.assertNotIn("mode=os.environ",source)

    def test_drift_cannot_start_or_qualify_for_cleanup(self):
        for key,value in [('replicaRetryLimit',1),('triggerType','Schedule')]:
            resource=deployment.definition(); resource['properties']['configuration'][key]=value
            with self.assertRaises(ValueError): deployment.validate_job(resource)
        resource=deployment.definition()
        resource['properties']['configuration']['secrets'][0]['keyVaultUrl']='https://other/secret'
        with self.assertRaises(ValueError): deployment.validate_job(resource)
        resource=deployment.definition(); resource['properties']['template']['containers'][0]['image']='mutable:latest'
        with self.assertRaises(ValueError): deployment.validate_job(resource)

    def test_resource_id_case_normalization_does_not_change_scope(self):
        resource=deployment.definition()
        resource['identity']['userAssignedIdentities']={k.lower():v for k,v in resource['identity']['userAssignedIdentities'].items()}
        for item in resource['properties']['configuration']['secrets']: item['identity']=item['identity'].lower()
        deployment.validate_job(resource)
