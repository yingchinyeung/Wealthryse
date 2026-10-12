import unittest
from copy import deepcopy
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import deploy_operational_findings_alert as deployment


class OperationalFindingsDeploymentTests(unittest.TestCase):
    def current(self):
        current=deployment.definition()
        current['id']=deployment.BASE+'Microsoft.Insights/scheduledQueryRules/'+deployment.RULE
        return current

    def test_exact_owner_route_and_scope(self):
        value=self.current()
        deployment.verify(value)
        self.assertEqual(value['properties']['actions']['actionGroups'],
            [deployment.BASE+'Microsoft.Insights/actionGroups/ibkr-v11-daily-alerts'])
        self.assertEqual(value['properties']['scopes'],
            [deployment.BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs'])

    def test_query_and_settings_drift_rejected(self):
        for key,replacement in [('enabled',False),('severity',0),('skipQueryValidation',True),
                                ('overrideQueryTimeRange','P2D')]:
            with self.subTest(key=key):
                current=deepcopy(self.current());current['properties'][key]=replacement
                with self.assertRaises(ValueError):deployment.verify(current)
        current=self.current();current['properties']['criteria']['allOf'][0]['query']='print healthy=true'
        with self.assertRaises(ValueError):deployment.verify(current)

    def test_cross_scope_and_notification_drift_rejected(self):
        for key in ('scopes','actions'):
            current=self.current()
            if key=='scopes':current['properties'][key]=['/unrelated/workspace']
            else:current['properties'][key]['actionGroups']=['/unrelated/action-group']
            with self.assertRaises(ValueError):deployment.verify(current)

    def test_no_financial_or_resource_mutation_authority(self):
        value=deployment.definition()
        self.assertNotIn('identity',value)
        self.assertEqual(set(value),{'location','kind','tags','properties'})
        self.assertNotIn('secrets',value['properties'])
        self.assertNotIn('identity',value['properties'])
        self.assertIn('No automatic retries or data changes.',value['properties']['description'])
        self.assertEqual(value['properties']['evaluationFrequency'],'PT15M')
        self.assertEqual(value['properties']['criteria']['allOf'][0]['failingPeriods'],
                         {'numberOfEvaluationPeriods':1,'minFailingPeriodsToAlert':1})

if __name__=='__main__':
    unittest.main()
