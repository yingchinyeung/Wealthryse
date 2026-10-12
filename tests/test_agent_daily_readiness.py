import unittest
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import patch
from ibkr_etl.agent_daily_readiness import publication_readiness
from ibkr_etl.specialist_report_runtime import TENANT


class PublicationReadinessTests(unittest.TestCase):
    def setUp(self): self.c=SimpleNamespace(autocommit=True,info=SimpleNamespace(transaction_status=0))
    def check(self,minute=15,active=True,report=None):
        with patch('ibkr_etl.agent_daily_readiness.get_daily_report',return_value=report), \
             patch('ibkr_etl.agent_daily_outbox.require_tenant'):
            return publication_readiness(self.c,tenant_id=TENANT,
                now=datetime(2026,10,3,10,minute,tzinfo=timezone.utc),schedule_active=active)
    def test_disabled_is_not_false_failure(self):
        self.assertEqual(self.check(active=False)['state'],'schedule_disabled')
        self.assertFalse(self.check(active=False)['action_required'])
    def test_grace_and_overdue_boundary(self):
        self.assertEqual(self.check(minute=14)['state'],'awaiting_publication')
        self.assertEqual(self.check()['state'],'publication_overdue')
        self.assertTrue(self.check()['action_required'])
    def test_available_is_not_email_delivery_or_active_schedule(self):
        result=self.check(active=False,report=dict(report_sha256='a'*64))
        self.assertEqual(result['state'],'available'); self.assertFalse(result['recipient_delivery_confirmed'])
        self.assertFalse(result['schedule_active'])
    def test_integrity_failure_is_not_empty_or_success(self):
        with patch('ibkr_etl.agent_daily_outbox.require_tenant'), \
             patch('ibkr_etl.agent_daily_readiness.get_daily_report',side_effect=ValueError('corrupt')):
            with self.assertRaises(ValueError): publication_readiness(self.c,tenant_id=TENANT,
                now=datetime(2026,10,3,10,tzinfo=timezone.utc),schedule_active=True)
    def test_bad_controls_rejected_before_database_read(self):
        base=dict(tenant_id=TENANT,now=datetime(2026,10,3,10,tzinfo=timezone.utc))
        for extra in ({'schedule_active':1},{'grace_minutes':0},{'grace_minutes':61},
                      {'grace_minutes':True},{'now':datetime(2026,10,3,10)}):
            with self.assertRaises(ValueError): publication_readiness(self.c,**{**base,**extra})
