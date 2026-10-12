import copy
import unittest
from datetime import datetime, timezone, timedelta
from control_upgrade_alert_test_20261010 import provider_completed, status_path, GROUP


class AlertAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime.now(timezone.utc) - timedelta(minutes=5)
        self.value = {'state': 'Complete', 'actionDetails': [{'MechanismType': 'Email', 'Name': 'operations-email',
                      'Status': 'Succeeded', 'SubState': None, 'SendTime': datetime.now(timezone.utc).isoformat()}]}

    def test_observed_success(self):
        self.assertTrue(provider_completed(self.value, self.start))

    def test_documented_success(self):
        self.value['state'] = 'Completed'
        self.value['actionDetails'][0].update(Status='Completed', SubState='Default')
        self.assertTrue(provider_completed(self.value, self.start))

    def test_inprogress_and_failures_never_pass(self):
        for status in ('InProgress', 'Failed', 'Disabled', None):
            value = copy.deepcopy(self.value)
            value['actionDetails'][0]['Status'] = status
            self.assertFalse(provider_completed(value, self.start))

    def test_wrong_receiver_and_error_detail(self):
        for fields in ({'Name': 'other'}, {'Detail': 'rejected'}, {'MechanismType': 'Webhook'}):
            value = copy.deepcopy(self.value)
            value['actionDetails'][0].update(fields)
            self.assertFalse(provider_completed(value, self.start))

    def test_timestamps_and_multiple_actions_rejected(self):
        for stamp in ('0001-01-01T00:00:00+00:00', 'invalid', '2026-10-10T00:00:00'):
            value = copy.deepcopy(self.value)
            value['actionDetails'][0]['SendTime'] = stamp
            self.assertFalse(provider_completed(value, self.start))
        self.value['actionDetails'] *= 2
        self.assertFalse(provider_completed(self.value, self.start))

    def test_status_scope(self):
        url = 'https://management.azure.com' + GROUP + '/notificationStatus/123?api-version=2021-09-01'
        self.assertTrue(status_path(url).endswith('api-version=2021-09-01'))
        for bad in (url.replace('management.azure.com', 'example.com'), url.replace('ibkr-v11-daily-alerts', 'other'), url + '&token=x'):
            with self.assertRaises(ValueError):
                status_path(bad)


if __name__ == '__main__':
    unittest.main()
