import unittest
from ibkr_etl.operational_alerts import metadata_findings_query
from ibkr_etl.operational_monitor import monitor_summary


class OperationalAlertTests(unittest.TestCase):
    def assessment(self, status='no_findings_in_checked_scope'):
        return {'status': status}

    def calendar(self, state='current_for_scheduled_cycle', quality='pass'):
        return {'mode':'shadow', 'checks':[{'state':state, 'quality':quality}]}

    def test_benchmark_failure_cannot_leave_successful_summary(self):
        for state in ('missing','future_observation','unexpected_non_session',
                      'behind_expected_session','calendar_unverified'):
            with self.subTest(state=state):
                result=monitor_summary(self.assessment(),self.calendar(state))
                self.assertEqual(result['status'],'needs_review')
                self.assertEqual(result['benchmark_attention'],1)

    def test_quality_warning_is_visible(self):
        result=monitor_summary(self.assessment(),self.calendar(quality='warn'))
        self.assertEqual(result['status'],'no_findings_in_checked_scope')
        self.assertEqual(result['benchmark_quality_warnings'],1)
        self.assertEqual(monitor_summary(self.assessment(),self.calendar(quality=None))['status'],
                         'needs_review')

    def test_queue_failure_cannot_be_hidden_by_current_benchmark(self):
        self.assertEqual(monitor_summary(self.assessment('needs_review'),self.calendar())['status'],
                         'needs_review')

    def test_current_checked_scope_is_not_financial_certification(self):
        result=monitor_summary(self.assessment(),self.calendar())
        self.assertEqual(result['status'],'no_findings_in_checked_scope')
        self.assertEqual(result['financial_reconciliation'],'not_certified')

    def test_empty_benchmark_scope_is_explicit(self):
        result=monitor_summary(self.assessment(),{'mode':'shadow','checks':[]})
        self.assertEqual(result['benchmark_scope'],'not_checked')

    def test_unknown_assessment_and_calendar_rejected(self):
        for assessment,calendar in [(self.assessment('healthy'),self.calendar()),
            (self.assessment(),{'mode':'shadow'}),(self.assessment(),self.calendar('invented')),
            (self.assessment(),{'mode':'live','checks':[]})]:
            with self.assertRaises(ValueError):
                monitor_summary(assessment,calendar)

    def test_alert_requires_exact_capture_and_complete_inventory(self):
        query=metadata_findings_query()
        self.assertIn('on Replica, CapturedAt',query)
        self.assertIn('IndexCount != Expected',query)
        self.assertIn('Variants != 1',query)
        self.assertIn('coalesce(tobool(Marker.inventory_complete), false) != true',query)
        self.assertIn('CapturedAt < ago(75m)',query)
        self.assertIn('CapturedAt > now()',query)
        self.assertIn('State != \'current_for_scheduled_cycle\'',query)
        self.assertIn('Captures == 2',query)
        self.assertIn('CapturedAt - First >= 20m',query)
        self.assertIn('IndexCount=array_length(Indices)',query)
        self.assertNotIn('dcount(',query)
        self.assertNotIn('retry',query)

if __name__=='__main__':
    unittest.main()
