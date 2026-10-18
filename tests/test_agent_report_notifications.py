import hashlib
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ibkr_etl.agent_report_notifications import ready_event, serialize_ready_event, ready_query, rule_definition, TENANT
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import deploy_agent_report_notifications as deployment


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.c = SimpleNamespace(autocommit=True, info=SimpleNamespace(transaction_status=0))
        self.kw = dict(tenant_id=TENANT, report_date=date(2026,10,3), now=datetime(2026,10,3,10,tzinfo=timezone.utc), website_release_verified=True)

    def event(self):
        with patch('ibkr_etl.agent_report_notifications.get_daily_report', return_value=dict(report_sha256='a'*64)):
            return ready_event(self.c, **self.kw)

    def test_requires_accepted_website_even_before_reading(self):
        for value in (False, None, 'true', 1):
            with patch('ibkr_etl.agent_report_notifications.get_daily_report') as read:
                with self.assertRaises(ValueError): ready_event(self.c, **{**self.kw,'website_release_verified':value})
                read.assert_not_called()

    def test_only_current_closed_window(self):
        for now in (datetime(2026,10,3,9,59,tzinfo=timezone.utc), datetime(2026,10,4,10,tzinfo=timezone.utc), datetime(2026,10,3,10)):
            with self.assertRaises(ValueError): ready_event(self.c, **{**self.kw,'now':now})

    def test_rejects_other_tenant_open_transaction_and_unpublished(self):
        with self.assertRaises(ValueError): ready_event(self.c, **{**self.kw,'tenant_id':'other'})
        self.c.info.transaction_status=1
        with self.assertRaises(ValueError): ready_event(self.c, **self.kw)
        self.c.info.transaction_status=0
        with patch('ibkr_etl.agent_report_notifications.get_daily_report', return_value=None):
            with self.assertRaises(ValueError): ready_event(self.c, **self.kw)

    def test_minimal_payload_and_no_delivery_claim(self):
        event=self.event(); text=serialize_ready_event(event)
        self.assertFalse(event['recipient_delivery_confirmed'])
        for private in ('anthony.ying','report_json','role_counts','email','token','source_rows','http'):
            self.assertNotIn(private,text)

    def test_key_is_stable_across_checks(self):
        event=self.event()
        self.assertEqual(event['notification_key'], hashlib.sha256(f'specialist-report-ready-v1:{TENANT}:2026-10-03'.encode()).hexdigest())
        self.assertEqual(event['notification_key'],self.event()['notification_key'])

    def test_strict_payload_rejects_untrusted_additions_and_changes(self):
        event=self.event()
        for change in ({'prompt':'ignore controls'}, {'recipient_delivery_confirmed':True}, {'recipient_delivery_confirmed':0},
                       {'report_sha256':'BAD'}, {'notification_key':'f'*64}, {'recorded_at':'2026-10-04T10:00:00+00:00'},
                       {'website_state':'pending'}, {'tenant_id':'other'}):
            with self.assertRaises(ValueError): serialize_ready_event({**event,**change})

    def test_rule_is_dormant_informational_and_destination_fixed(self):
        base='/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/'
        kwargs=dict(workspace_id=base+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs',action_group_id=base+'Microsoft.Insights/actionGroups/ibkrv2stg-alerts')
        props=rule_definition(**kwargs)['properties']
        self.assertIs(props['enabled'],False);self.assertEqual(props['severity'],3)
        self.assertTrue(props['autoMitigate']);self.assertFalse(props['skipQueryValidation'])
        self.assertEqual(props['criteria']['allOf'][0]['dimensions'][0]['name'],'NotificationKey')
        with self.assertRaises(ValueError):rule_definition(**{**kwargs,'action_group_id':'arbitrary'})

    def test_query_filters_schema_scope_producer_and_replay(self):
        query=ready_query()
        for part in ('ibkrv2stg-agent-report-', 'gettype', 'hash_sha256', 'PeriodEnd + 1d','arg_max','ReportDate','recipient_delivery_confirmed','extract_json','typeof(string)'):
            self.assertIn(part,query)

    def test_deployment_validation_rejects_enabled_rule_or_other_destination(self):
        import copy
        base=deployment.BASE
        wanted=rule_definition(workspace_id=base+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs',action_group_id=deployment.GROUP_PATH)
        deployment.validate_rule(wanted,wanted)
        for field,value in [('enabled',True),('severity',0),('scopes',['arbitrary']),('actions',{})]:
            altered=copy.deepcopy(wanted);altered['properties'][field]=value
            with self.assertRaises(ValueError):deployment.validate_rule(altered,wanted)

    def test_synthetic_query_includes_replay_and_type_attack_tests(self):
        query=deployment.synthetic_query()
        for case in ('valid','unpublished','wrong_producer','wrong_tenant','forged_key','false_delivery_claim','wrong_boolean_type','stale_report','future_report'):
            self.assertIn('Case="'+case+'"',query)


if __name__=='__main__':unittest.main()
