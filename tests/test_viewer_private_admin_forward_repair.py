import copy,unittest
import deploy_viewer_private_admin_20261008 as p
from repair_viewer_private_admin_20261008 import correction,check_changes,rules_match,SUPPORT,support_scope

class PrivatePathForwardTests(unittest.TestCase):
    def test_exactly_two_new_nsg_definitions_change(self):
        before=p.template('ssh-ed25519 fixture','fixed-version');after=correction(before)
        prior={x['name']:x for x in before['resources']}
        changed=[x['name'] for x in after['resources'] if {k:v for k,v in x.items() if k!='dependsOn'}!={k:v for k,v in prior[x['name']].items() if k!='dependsOn'}]
        self.assertEqual(changed,['ibkrv2stg-bastion-nsg','ibkrv2stg-admin-nsg'])
        admin=next(x for x in after['resources'] if x['name']=='ibkrv2stg-admin-nsg')
        self.assertFalse(any(r['properties']['destinationAddressPrefix']=='AzurePlatformDNS' for r in admin['properties']['securityRules']))
        self.assertEqual(p.expected_ids(after),p.expected_ids(before)-SUPPORT)
        self.assertTrue(all(not any(x in SUPPORT for x in r['dependsOn']) for r in after['resources']))
    def test_only_owned_bastion_rule_modify_is_eligible(self):
        ids=p.expected_ids(p.template('public-fixture','version'))
        changes=[{'resourceId':i,'changeType':'Create'} for i in ids]
        bastion=p.rid('Microsoft.Network/networkSecurityGroups','ibkrv2stg-bastion-nsg')
        chosen=next(x for x in changes if x['resourceId']==bastion)
        chosen.update(changeType='Modify',delta=[{'path':'properties.securityRules'}])
        check_changes(changes,ids)
        for identity,path in ((bastion,'properties.unrelated'),('existing-production','properties.securityRules')):
            bad=copy.deepcopy(changes);item=next(x for x in bad if x['resourceId']==bastion);item['resourceId']=identity;item['delta'][0]['path']=path
            with self.assertRaises(ValueError):check_changes(bad,ids)
    def test_rules_allow_api_metadata_not_semantic_changes(self):
        rules=[p.rule('fixture',100,'Inbound','private','*','22')]
        actual=copy.deepcopy(rules);actual[0]['properties']['provisioningState']='Succeeded'
        self.assertTrue(rules_match(actual,rules))
        actual[0]['properties']['sourceAddressPrefix']='Internet'
        self.assertFalse(rules_match(actual,rules))
    def test_retained_support_preserves_ddos_sku_and_allocation(self):
        source={'id':'owned-pip','sku':{'name':'Standard','tier':'Regional'},'properties':{'ddosSettings':{'protectionMode':'VirtualNetworkInherited'},'ipAddress':'public-fixture','ipConfiguration':{'id':'new-association'}}}
        changed=copy.deepcopy(source);changed['properties']['ddosSettings']={'protectionMode':'Disabled'}
        self.assertNotEqual(support_scope(source),support_scope(changed))
        changed=copy.deepcopy(source);changed['properties']['ipConfiguration']={'id':'different-runtime-association'}
        self.assertEqual(support_scope(source),support_scope(changed))

if __name__=='__main__':unittest.main()
