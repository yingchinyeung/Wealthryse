import hashlib
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from enroll_specialist_gateway_authority import enroll,verify_intent,TENANT,AGENT_PRINCIPAL,POLICY_VERSION

OWNER='00000000-0000-0000-0000-000000000040'
NOW=datetime(2026,10,2,tzinfo=timezone.utc)


def intent():
    return dict(schema='specialist-gateway-authority-enrollment-v1',operation_id='71963415-8457-41a9-a665-2b37c793b32d',
        owner_sha256=hashlib.sha256(OWNER.encode()).hexdigest(),source_sha256='a'*64,tenant_id=TENANT,
        principal_id=AGENT_PRINCIPAL,subject_ids=['U16088571'],tool_names=['get_performance_series'],
        expires_at=(NOW+timedelta(days=7)).isoformat())


class Connection:
    def __init__(self):self.sql=[];self.controls=[];self.grants=[];self.owner=True;self.subject=True;self.writes=[]
    @contextmanager
    def transaction(self):yield
    def execute(self,sql,args=()):
        self.sql.append(sql);one=None;rows=[]
        if 'AS now' in sql:one={'now':NOW}
        elif 'FROM app_users' in sql:rows=[{'entra_object_id':OWNER}] if self.owner else []
        elif 'FROM platform_subjects' in sql:rows=[{'subject_id':'U16088571'}] if self.subject else []
        elif 'FROM specialist_observation_controls' in sql:rows=self.controls
        elif 'FROM specialist_observation_grants' in sql:rows=self.grants
        elif sql.startswith('INSERT'):self.writes.append((sql,args))
        elif not sql.startswith(('SET TRANSACTION','SELECT set_config')):raise AssertionError(sql)
        return SimpleNamespace(fetchone=lambda:one,fetchall=lambda:rows)


class EnrollmentTests(unittest.TestCase):
    def test_only_two_fixed_authority_inserts(self):
        c=Connection();self.assertTrue(enroll(c,intent()))
        self.assertEqual(len(c.writes),2)
        self.assertTrue(all('specialist_observation_' in s for s,_ in c.writes))
        self.assertFalse(any(s.startswith(('UPDATE','DELETE','GRANT','ALTER')) for s in c.sql))
        self.assertIn('SERIALIZABLE',c.sql[0])

    def test_user_revocation_missing_subject_expired_or_unbounded_denies_before_write(self):
        for c,changes in [(Connection(),{'expires_at':NOW.isoformat()}),(Connection(),{'expires_at':(NOW+timedelta(days=9)).isoformat()})]:
            with self.assertRaises(ValueError):enroll(c,{**intent(),**changes})
            self.assertFalse(c.writes)
        for flag in ('owner','subject'):
            c=Connection();setattr(c,flag,False)
            with self.assertRaises(ValueError):enroll(c,intent())
            self.assertFalse(c.writes)

    def test_cross_tenant_principal_scope_tool_or_extra_input_rejected(self):
        for changes in ({'tenant_id':'other'},{'principal_id':OWNER},{'subject_ids':['*']},
            {'tool_names':['execute_sql']},{'background_allowed':True},{'owner_sha256':'fake'}):
            with self.assertRaises(ValueError):verify_intent({**intent(),**changes})

    def test_exact_replay_readonly_but_stop_revision_or_scope_change_never_reset(self):
        c=Connection();value=intent()
        c.controls=[dict(kill_switch=False,policy_version=POLICY_VERSION,revision=1)]
        c.grants=[dict(principal_id=AGENT_PRINCIPAL,specialist_role='data_analyst',subject_ids=['U16088571'],
            resource_ids=[],tool_names=['get_performance_series'],enabled=True,background_allowed=False,
            expires_at=datetime.fromisoformat(value['expires_at']),policy_version=POLICY_VERSION,revision=1)]
        self.assertFalse(enroll(c,value));self.assertFalse(c.writes)
        for change in ({'kill_switch':True},{'revision':2}):
            old=dict(c.controls[0]);c.controls[0].update(change)
            with self.assertRaises(ValueError):enroll(c,value)
            self.assertFalse(c.writes);c.controls[0]=old
        c.grants[0]['enabled']=False
        with self.assertRaises(ValueError):enroll(c,value)
        self.assertFalse(c.writes)
