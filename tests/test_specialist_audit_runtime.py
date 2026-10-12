import base64
import copy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,MagicMock,patch

from ibkr_etl import specialist_audit_runtime as runtime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import deploy_specialist_audit_worker as deployment
import provision_specialist_audit_login as provisioner


def connection(changes=None):
    rows=[dict(role=runtime.ROLE,login=runtime.LOGIN,tenant=runtime.TENANT),
          [dict(rolname=n,rolcanlogin=n==runtime.LOGIN,rolinherit=False,rolsuper=False,rolcreatedb=False,
                rolcreaterole=False,rolreplication=False,rolbypassrls=False) for n in (runtime.LOGIN,runtime.ROLE)],
          [{'rolname':runtime.LOGIN},{'rolname':runtime.ROLE}],
          [{'role':runtime.ROLE,'member':runtime.LOGIN,'admin_option':False,'inherit_option':False,'set_option':True}]]
    for principal in (runtime.LOGIN,runtime.ROLE):
        rows.extend([{'n':0},{'n':0},{'n':0},{'allowed':False},
                     {'read':principal==runtime.ROLE,'insert':principal==runtime.ROLE,'forbidden':False}])
    rows.extend([{'relrowsecurity':True,'relforcerowsecurity':True},
                 [{'tgname':name,'tgenabled':'O','proname':'guard_specialist_audit'} for name in
                  ('specialist_audit_append_only','specialist_audit_insert_guard')]])
    for i,value in (changes or {}).items(): rows[i]=value
    c=Mock(); responses=iter(rows)
    def execute(*args):
        value=next(responses)
        return SimpleNamespace(fetchone=lambda:value,fetchall=lambda:value)
    c.execute.side_effect=execute
    return c


class AuditRuntimeTests(unittest.TestCase):
    def test_append_only_identity_passes_without_writing(self):
        c=connection(); result=runtime.verify_audit_access(c)
        self.assertTrue(result['ledger_append_only']); self.assertEqual(result['events_written'],0)
        self.assertFalse(result['source_table_access'])
        self.assertTrue(all(call.args[0].lstrip().startswith('SELECT') for call in c.execute.call_args_list))

    def test_reachable_or_administrable_role_expansion_rejected(self):
        for index,value in [(2,[{'rolname':runtime.LOGIN},{'rolname':runtime.ROLE},{'rolname':'administrator'}]),
                            (3,[{'role':runtime.ROLE,'member':runtime.LOGIN,'admin_option':True,'inherit_option':False,'set_option':True}])]:
            with self.assertRaises(ValueError): runtime.verify_audit_access(connection({index:value}))

    def test_direct_login_and_capability_access_both_checked(self):
        for index in (4,5,6,9,10,11):
            with self.subTest(index=index),self.assertRaises(ValueError):
                runtime.verify_audit_access(connection({index:{'n':1}}))
        for index in (7,12):
            with self.assertRaises(ValueError): runtime.verify_audit_access(connection({index:{'allowed':True}}))

    def test_rewrite_privileges_and_missing_guards_rejected(self):
        for index,value in [(8,{'read':True,'insert':True,'forbidden':False}),
                            (13,{'read':True,'insert':True,'forbidden':True}),
                            (14,{'relrowsecurity':True,'relforcerowsecurity':False}),(15,[])]:
            with self.assertRaises(ValueError): runtime.verify_audit_access(connection({index:value}))

    def test_authenticated_identity_not_admin_set_role(self):
        c=Mock(autocommit=True,info=SimpleNamespace(transaction_status=0))
        c.execute.return_value.fetchone.return_value={'login':'admin'}
        with self.assertRaises(ValueError): runtime.bind_audit_session(c)
        self.assertEqual(c.execute.call_count,1)

    def test_destination_and_ssl_are_fixed(self):
        value=f'postgresql://{runtime.LOGIN}:synthetic@{runtime.HOST}:5432/{runtime.DATABASE}?sslmode=require'
        runtime.validate_database_url(value)
        for other in (value.replace(runtime.LOGIN,'admin'),value.replace(runtime.HOST,'other'),
                      value.replace('require','disable'),value+'&options=',value+'#other'):
            with self.assertRaises(ValueError): runtime.validate_database_url(other)


class AuditDeploymentTests(unittest.TestCase):
    def test_worker_is_separate_and_manual_with_no_source_or_admin_credentials(self):
        resource=deployment.definition(); deployment.validate(resource)
        self.assertEqual(resource['properties']['configuration']['triggerType'],'Manual')
        self.assertEqual(resource['properties']['configuration']['replicaRetryLimit'],0)
        self.assertNotIn('report-url',str(resource)); self.assertNotIn('database-admin-url',str(resource))
        self.assertNotIn('ingestion-identity',str(resource))
        self.assertEqual(len(resource['properties']['configuration']['secrets']),1)
        for name in (deployment.JOB,deployment.PROVISION): self.assertLessEqual(len(name),32)

    def test_payload_compiles_and_worker_cannot_append_events(self):
        for provision in (False,True):
            args,digest=deployment.bundle(provision)
            source=base64.b64decode(args[1].split("b64decode('")[1].split("')")[0]).decode()
            compile(source,'audit','exec')
            self.assertLess(len(args[1]),30000)
            self.assertEqual(len(digest),64)
            if not provision:
                self.assertNotIn('append_event',source)
                self.assertNotIn('INSERT INTO',source)

    def test_drift_blocks_execution(self):
        original=deployment.definition()
        for key,value in [('triggerType','Schedule'),('replicaRetryLimit',1)]:
            resource=copy.deepcopy(original); resource['properties']['configuration'][key]=value
            with self.assertRaises(ValueError): deployment.validate(resource)
        resource=copy.deepcopy(original);resource['identity']['type']='SystemAssigned, UserAssigned'
        with self.assertRaises(ValueError): deployment.validate(resource)
        resource=copy.deepcopy(original);resource['properties']['template']['initContainers']=[{'name':'other'}]
        with self.assertRaises(ValueError): deployment.validate(resource)

    def test_failed_new_login_is_quarantined(self):
        admin=MagicMock();admin.__enter__.return_value=admin
        admin.info.host=runtime.HOST;admin.info.dbname=runtime.DATABASE
        admin.execute.return_value.fetchone.return_value=None
        value=f'postgresql://{runtime.LOGIN}:synthetic@{runtime.HOST}:5432/{runtime.DATABASE}?sslmode=require'
        with patch.dict(provisioner.os.environ,{'DATABASE_URL':'admin-test','SPECIALIST_AUDIT_DATABASE_URL':value}), \
             patch.object(provisioner.psycopg,'connect',side_effect=[admin,RuntimeError('new login failed')]):
            with self.assertRaises(RuntimeError): provisioner.main()
        self.assertEqual(admin.execute.call_args.args[0],'ALTER ROLE ibkr_specialist_audit_login NOLOGIN')

    def test_existing_login_is_never_implicitly_changed(self):
        admin=MagicMock();admin.__enter__.return_value=admin
        admin.info.host=runtime.HOST;admin.info.dbname=runtime.DATABASE
        admin.execute.return_value.fetchone.return_value={'exists':True}
        value=f'postgresql://{runtime.LOGIN}:synthetic@{runtime.HOST}:5432/{runtime.DATABASE}?sslmode=require'
        with patch.dict(provisioner.os.environ,{'DATABASE_URL':'admin-test','SPECIALIST_AUDIT_DATABASE_URL':value}), \
             patch.object(provisioner.psycopg,'connect',return_value=admin):
            with self.assertRaises(ValueError): provisioner.main()
        self.assertEqual(admin.execute.call_count,1)


if __name__=='__main__': unittest.main()
