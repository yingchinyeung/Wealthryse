import unittest
from ibkr_etl.agent_report_watchdog import missing_query, watchdog_definition

BASE='/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/'


class WatchdogTests(unittest.TestCase):
    def test_absence_query_has_independent_expected_row_and_grace(self):
        q = missing_query()
        for text in ('print ReportDate=ExpectedDate', '2026-10-03T10:00:00Z', 'ExpectedEnd + 15m',
                     'ValidatedPublications == 0', 'specialist-report-ready-v1', 'recipient_delivery_confirmed'):
            self.assertIn(text, q)

    def test_fixed_destination_and_activation(self):
        args=dict(workspace_id=BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs',
                  action_group_id=BASE+'Microsoft.Insights/actionGroups/ibkrv2stg-alerts')
        dormant=watchdog_definition(**args); live=watchdog_definition(**args, enabled=True)
        self.assertFalse(dormant['properties']['enabled']); self.assertTrue(live['properties']['enabled'])
        self.assertEqual(live['properties']['severity'], 2)
        self.assertEqual(live['properties']['criteria']['allOf'][0]['dimensions'][0]['name'], 'ReportDate')
        with self.assertRaises(ValueError): watchdog_definition(**args, enabled=1)
        with self.assertRaises(ValueError): watchdog_definition(**{**args, 'action_group_id':'other'})
