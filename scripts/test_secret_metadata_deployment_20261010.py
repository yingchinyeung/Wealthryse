"""No-Azure regression checks for additive metadata monitoring boundaries."""
import copy,json,unittest
from unittest.mock import patch
import control_secret_metadata_monitor_20261010 as c
import alert_secret_metadata_monitor_20261010 as a


class MetadataDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.identity={'properties':{'clientId':'00000000-0000-0000-0000-000000000001','principalId':'00000000-0000-0000-0000-000000000002'}}
    def test_exact_metadata_grants(self):
        roles=c.roles(self.identity['properties']['principalId'])
        self.assertEqual(len(roles),4)
        self.assertEqual(sum(x['body']['properties']['roleDefinitionId'].endswith(c.READER) for x in roles),3)
        self.assertEqual(sum(x['body']['properties']['roleDefinitionId'].endswith(c.PULL) for x in roles),1)
        self.assertTrue(all('/providers/Microsoft.KeyVault/vaults/' in x['scope'] or x['scope']==c.REGISTRY for x in roles))
    def test_grants_deterministic(self):self.assertEqual(c.roles('a'),c.roles('a'))
    def test_manual_before_acceptance(self):self.assertEqual(c.body(self.identity)['properties']['configuration']['triggerType'],'Manual')
    def test_schedule(self):self.assertEqual(c.body(self.identity,True)['properties']['configuration']['scheduleTriggerConfig']['cronExpression'],'15 6 * * *')
    def test_no_credential_env(self):
        b=c.body(self.identity);self.assertEqual(b['properties']['configuration']['secrets'],[])
        self.assertEqual([e['name'] for e in b['properties']['template']['containers'][0]['env']],['AZURE_CLIENT_ID'])
    def test_no_retry(self):self.assertEqual(c.body(self.identity)['properties']['configuration']['replicaRetryLimit'],0)
    def test_wrong_role_rejected(self):
        wanted=c.roles('a')[0];actual={'properties':{**wanted['body']['properties'],'scope':wanted['scope']}}
        self.assertTrue(c.role_match(actual,wanted));actual['properties']['roleDefinitionId']='secret-user';self.assertFalse(c.role_match(actual,wanted))
    def test_wrong_scope_rejected(self):
        wanted=c.roles('a')[0];actual={'properties':{**wanted['body']['properties'],'scope':'/subscriptions/other'}}
        self.assertFalse(c.role_match(actual,wanted))
    def test_single_existing_owner_route(self):self.assertEqual(a.definition()['properties']['actions']['actionGroups'],[c.BASE+'Microsoft.Insights/actionGroups/ibkr-v11-daily-alerts'])
    def test_alert_no_sensitive_projection(self):
        self.assertNotIn('secret_name',a.query());self.assertNotIn('tags',a.query());self.assertIn('FindingCount',a.query())
    def test_fault_cases_complete(self):self.assertEqual(len(list(a.cases())),26)
    def test_alert_missing_arrays_checked(self):self.assertIn('isnull(array_length(E.errors))',a.query())
    def test_alert_union_numeric_type_consistent(self):
        self.assertIn('FindingCount=tolong(E.finding_count)',a.query());self.assertIn('FindingCount=long(0)',a.query())
    def test_observed_empty_azure_defaults_only(self):
        job=c.body(self.identity);job['id']=c.JOBPATH;job['properties']['provisioningState']='Succeeded'
        job['properties']['configuration']['secrets']=None
        job['properties']['configuration']['registries'][0].update(username='',passwordSecretRef='')
        job['properties']['template']['containers'][0]['resources']['ephemeralStorage']=''
        c.verify_job(job,self.identity)
        job['properties']['configuration']['registries'][0]['passwordSecretRef']='unexpected-password'
        with self.assertRaises(ValueError):c.verify_job(job,self.identity)
    def test_extra_runtime_setting_rejected(self):
        job=c.body(self.identity);job['id']=c.JOBPATH;job['properties']['provisioningState']='Succeeded'
        job['properties']['template']['containers'][0]['resources']['ephemeralStorage']='2Gi'
        with self.assertRaises(ValueError):c.verify_job(job,self.identity)


if __name__=='__main__':unittest.main()
