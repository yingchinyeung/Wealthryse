import copy
import unittest
from unittest.mock import patch
import apply_reporting_retirement_20261011 as a

class RetirementGuards(unittest.TestCase):
    def alert(self):
        return {'id':a.ALERT_PATH,'name':a.ALERT,'location':'global','properties':{
            'enabled':True,'scopes':[a.r.path_for('ibkrv2stg-historical-reports')],
            'criteria':{'odata.type':'Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria'},'actions':[{'actionGroupId':'preserved'}]}}
    def job(self):
        name='ibkrv2stg-historical-reports'
        return {'name':name,'location':'eastasia','identity':{'type':'UserAssigned','userAssignedIdentities':{'retained':{}}},
                'properties':{'configuration':{'triggerType':'Manual','provisioningState':'Succeeded'},
                    'template':{'containers':[{'image':a.p.IMAGES[name],'env':[]}]}}}
    def test_exact_targets(self):
        self.assertEqual(len(a.p.IMAGES),2)
        for n in ('ibkrv2stg-web','ibkrv2stg-onboard-render','../other'):
            with self.assertRaises(ValueError):a.r.path_for(n)
    def test_new_caller_rejected(self):
        with self.assertRaises(ValueError):a.guard_dependencies([{'kind':'app_job','caller':'web'}])
    def test_dedicated_monitor_only(self):
        a.guard_dependencies([{'kind':'Microsoft.Insights/metricAlerts','caller':a.ALERT,'targets':['ibkrv2stg-historical-reports']}])
    def test_shared_alert_rejected(self):
        v=self.alert();v['properties']['scopes'].append('/another/job')
        with self.assertRaises(ValueError):a.alert_body(v,False)
    def test_alert_preserves_actions(self):
        v=self.alert();before=copy.deepcopy(v);body=a.alert_body(v,False)
        self.assertEqual(v,before);self.assertFalse(body['properties']['enabled'])
        self.assertEqual(body['properties']['actions'],v['properties']['actions'])
    def test_inline_secret_rejected(self):
        v=self.job();v['properties']['template']['containers'][0]['env']=[{'name':'DATABASE_URL','value':'forbidden'}]
        with self.assertRaises(ValueError):a.r.no_inline_secrets(v)
    def test_system_identity_rejected(self):
        v=self.job();v['identity']={'type':'SystemAssigned'}
        with self.assertRaises(ValueError):a.r.restore_body(v)
    def test_wrong_image_rejected(self):
        v=self.job();v['properties']['template']['containers'][0]['image']='wrong'
        with self.assertRaises(ValueError):a.r.restore_body(v)
    def test_non_target_drift_rejected(self):
        with patch.object(a.r,'bindings',return_value={'web':'new'}):
            with self.assertRaises(ValueError):a.r.preserve_others([],{'non_target_bindings':{'web':'old'}})
    def test_local_default_targets_removed(self):a.local_scope()

if __name__=='__main__':unittest.main()
