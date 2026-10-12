import copy,unittest
import deploy_viewer_private_admin_20261008 as p
from repair_viewer_private_admin_extension_20261008 import correction,check_changes,stable,without_etag

class ExtensionRepairTests(unittest.TestCase):
    def test_only_setting_and_dependencies_change(self):
        before=p.template('public-fixture','version');after=correction(before)
        self.assertEqual(len(after['resources']),2)
        ext=after['resources'][0]
        self.assertNotIn('enableAutomaticUpgrade',ext['properties'])
        self.assertTrue(ext['properties']['autoUpgradeMinorVersion'])
        self.assertEqual(ext['dependsOn'],[])
        self.assertEqual(after['resources'][1]['scope'],p.rid('Microsoft.Compute/virtualMachines',p.VM))
        self.assertIn('enableAutomaticUpgrade',before['resources'][-3]['properties'])
    def test_rejects_other_principal_or_scope(self):
        before=p.template('public-fixture','version')
        role=next(r for r in before['resources'] if r['type']=='Microsoft.Authorization/roleAssignments')
        role['properties']['principalId']='another-owner'
        with self.assertRaises(ValueError):correction(before)
    def test_no_existing_resources_may_change(self):
        ids=p.expected_ids(correction(p.template('public-fixture','version')))
        changes=[{'resourceId':i,'changeType':'Create'} for i in ids]
        check_changes(changes,ids)
        bad=copy.deepcopy(changes);bad[0]['changeType']='Modify'
        with self.assertRaises(ValueError):check_changes(bad,ids)
        with self.assertRaises(ValueError):check_changes(changes+[{'resourceId':p.VNET,'changeType':'Delete'}],ids)
    def test_baseline_preserves_security(self):
        self.assertEqual(stable({'etag':'runtime','properties':{'provisioningState':'Updating','secureBoot':True}}),{'properties':{'secureBoot':True}})
        self.assertNotEqual(stable({'properties':{'secureBoot':True}}),stable({'properties':{'secureBoot':False}}))
    def test_network_etags_not_permissions_or_readiness_are_ignored(self):
        prior={'delegations':[{'etag':'before','properties':{'serviceName':'Microsoft.App/environments','provisioningState':'Succeeded','actions':['join']}}]}
        current=copy.deepcopy(prior);current['delegations'][0]['etag']='after'
        self.assertEqual(without_etag(prior),without_etag(current))
        current['delegations'][0]['properties']['actions']=['different']
        self.assertNotEqual(without_etag(prior),without_etag(current))
        current=copy.deepcopy(prior);current['delegations'][0]['properties']['provisioningState']='Failed'
        self.assertNotEqual(without_etag(prior),without_etag(current))

if __name__=='__main__':unittest.main()
