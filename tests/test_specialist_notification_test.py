import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from test_specialist_azure_notification import status_path,provider_completion,GROUP_PATH
from enable_specialist_ready_notification import wanted,verify_only_activation_changed


class AzureNotificationTestGuards(unittest.TestCase):
    def test_only_exact_server_api_stamp_change_is_allowed(self):
        before=dict(enabled=False,createdWithApiVersion='2023-12-01',severity=3)
        after=dict(enabled=True,createdWithApiVersion='2021-08-01',severity=3)
        self.assertEqual(verify_only_activation_changed(before,after)['current'],'2021-08-01')
        for change in ({'severity':0},{'createdWithApiVersion':'other'},{'enabled':1},{'new_setting':True}):
            with self.assertRaises(ValueError): verify_only_activation_changed(before,{**after,**change})
    def test_activation_definition_changes_only_enabled_flag(self):
        off=wanted(False);on=wanted(True)
        self.assertIs(off['properties']['enabled'],False)
        self.assertIs(on['properties']['enabled'],True)
        on['properties']['enabled']=False
        self.assertEqual(off,on)
        for bad in (1,'true',None):
            with self.assertRaises(ValueError): wanted(bad)
    def test_only_exact_azure_status_destination(self):
        url='https://management.azure.com'+GROUP_PATH+'/notificationStatus/1234?api-version=2021-09-01'
        self.assertEqual(status_path(url),GROUP_PATH+'/notificationStatus/1234?api-version=2021-09-01')
        for bad in (url.replace('https:','http:'),url.replace('management.azure.com','evil.example'),
                url.replace('1234','../../secret'),url+'&token=private',url+'#fragment',
                url.replace('ibkr-v11-daily-alerts','other-group')):
            with self.assertRaises(ValueError): status_path(bad)

    def test_exact_provider_success_is_not_recipient_delivery(self):
        value=dict(state='Completed',actionDetails=[dict(MechanismType='Email',Name='operations-email',
            Status='Completed',SubState='Default',SendTime='2026-10-03T01:00:00Z')])
        self.assertTrue(provider_completion(value))
        self.assertFalse(provider_completion(dict(value,state='Running')))
        self.assertFalse(provider_completion(dict(value,actionDetails=value['actionDetails']*2)))
        for field in ('MechanismType','Name','Status','SubState','SendTime'):
            changed=dict(value['actionDetails'][0]);changed[field]=''
            self.assertFalse(provider_completion(dict(value,actionDetails=[changed])))
