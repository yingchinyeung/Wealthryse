import unittest
from datetime import date
from types import SimpleNamespace
from ibkr_etl.agent_daily_outbox import claim_delivery, start_send, finish_send, require_tenant

TENANT = '00000000-0000-0000-0000-000000000001'


class Connection:
    def __init__(self): self.statements=[]
    def execute(self,sql,params=()):
        self.statements.append((sql,params))
        return SimpleNamespace(fetchone=lambda: {'tenant_id':TENANT} if 'app_tenant_id()' in sql else None)


class OutboxTests(unittest.TestCase):
    def setUp(self): self.db=Connection()
    def test_wrong_tenant_has_no_writes(self):
        with self.assertRaises(ValueError): require_tenant(self.db,'wrong')
        self.assertEqual(len(self.db.statements),1)
    def test_claim_limits(self):
        for seconds in [True,0,14,901]:
            with self.assertRaises(ValueError): claim_delivery(self.db,tenant_id=TENANT,channel='email',lease_seconds=seconds)
    def test_unregistered_channel_rejected(self):
        with self.assertRaises(ValueError): claim_delivery(self.db,tenant_id=TENANT,channel='sms')
    def test_lease_conflict_stops_send(self):
        with self.assertRaises(ValueError): start_send(self.db,tenant_id=TENANT,report_date=date(2026,10,2),channel='email',lease_token='one')
    def test_success_cannot_be_inferred(self):
        for outcome in ['queued','sent','accepted','delivered']:
            with self.assertRaises(ValueError): finish_send(self.db,tenant_id=TENANT,report_date=date(2026,10,2),channel='email',lease_token='one',outcome=outcome)
    def test_claim_never_selects_sending_or_uncertain(self):
        self.assertIsNone(claim_delivery(self.db,tenant_id=TENANT,channel='email'))
        sql=self.db.statements[-1][0]
        self.assertIn('FOR UPDATE SKIP LOCKED',sql)
        self.assertIn("state='pending' OR (state='leased'",sql)
        self.assertNotIn("state='sending'",sql)
        self.assertNotIn("state='uncertain'",sql)
