import copy,unittest
from deploy_viewer_private_admin_20261008 import template,expected_ids,safe_changes,check_prefixes,existing_network_scope,OWNER,ROLE,VM,BASE

class PrivateAdminTests(unittest.TestCase):
    def setUp(self):
        self.value=template('ssh-ed25519 fixture-public-only','24.04.fixture')
        self.vnet={'id':'fixture-vnet','location':'eastasia','properties':{'addressSpace':{'addressPrefixes':['10.50.0.0/16']},'subnets':[{'name':'container-apps','properties':{'addressPrefix':'10.50.0.0/23','delegations':[{'name':'preserve'}]}},{'name':'postgres','properties':{'addressPrefix':'10.50.4.0/28'}}]}}
    def test_vm_has_no_public_ip_or_password_and_secure_boot(self):
        vm=next(r for r in self.value['resources'] if r['type']=='Microsoft.Compute/virtualMachines')
        self.assertNotIn('adminPassword',vm['properties']['osProfile'])
        self.assertTrue(vm['properties']['osProfile']['linuxConfiguration']['disablePasswordAuthentication'])
        self.assertTrue(vm['properties']['securityProfile']['uefiSettings']['secureBootEnabled'])
        nic=next(r for r in self.value['resources'] if r['type']=='Microsoft.Network/networkInterfaces')
        self.assertNotIn('publicIPAddress',nic['properties']['ipConfigurations'][0]['properties'])
    def test_role_only_owner_and_only_new_vm(self):
        roles=[r for r in self.value['resources'] if r['type']=='Microsoft.Authorization/roleAssignments']
        self.assertEqual(len(roles),1)
        self.assertEqual(roles[0]['properties']['principalId'],OWNER)
        self.assertTrue(roles[0]['scope'].endswith('/virtualMachines/'+VM))
        self.assertTrue(roles[0]['properties']['roleDefinitionId'].endswith('/'+ROLE))
        self.assertIn(roles[0]['scope']+'/providers/Microsoft.Authorization/roleAssignments/'+roles[0]['name'],expected_ids(self.value))
    def test_only_dedicated_subnets_never_replace_existing_vnet(self):
        types=[r['type'] for r in self.value['resources']]
        self.assertNotIn('Microsoft.Network/virtualNetworks',types)
        subnets=[r for r in self.value['resources'] if r['type']=='Microsoft.Network/virtualNetworks/subnets']
        self.assertEqual(len(subnets),2)
        admin=next(r for r in subnets if r['name'].endswith('/private-administration'))
        self.assertFalse(admin['properties']['defaultOutboundAccess'])
        self.assertIn('natGateway',admin['properties'])
        ids=expected_ids(self.value)
        self.assertTrue(any(x.endswith('/virtualNetworks/ibkrv2stg-vnet/subnets/private-administration') for x in ids))
        self.assertTrue(any(x.endswith('/virtualMachines/'+VM+'/extensions/AADSSHLoginForLinux') for x in ids))
        check_prefixes(self.vnet)
        overlap=copy.deepcopy(self.vnet);overlap['properties']['subnets'].append({'name':'other','properties':{'addressPrefix':'10.50.8.0/24'}})
        with self.assertRaises(ValueError):check_prefixes(overlap)
    def test_readback_preserves_existing_subnet_permissions(self):
        changed=copy.deepcopy(self.vnet);changed['properties']['subnets'][0]['properties']['delegations']=[]
        self.assertNotEqual(existing_network_scope(changed),existing_network_scope(self.vnet))
    def test_what_if_rejects_existing_changes_deletion_and_partial_scope(self):
        ids=expected_ids(self.value)
        changes=[{'resourceId':x,'changeType':'Create'} for x in ids]
        safe_changes(changes,ids)
        safe_changes(changes+[{'resourceId':BASE+'Microsoft.App/containerApps/existing','changeType':'Ignore','delta':None}],ids)
        for bad in (changes[:-1],changes+[{'resourceId':'existing-production','changeType':'Modify'}],[{**changes[0],'changeType':'Delete'}]+changes[1:]):
            with self.assertRaises(ValueError):safe_changes(bad,ids)
        for ignored in ({'resourceId':BASE+'Microsoft.App/containerApps/existing','changeType':'Ignore','delta':[{'propertyChangeType':'Modify'}]}, {'resourceId':'other-resource-group/existing','changeType':'Ignore'}):
            with self.assertRaises(ValueError):safe_changes(changes+[ignored],ids)
    def test_bastion_no_shared_link_and_vm_ssh_only_from_bastion(self):
        bastion=next(r for r in self.value['resources'] if r['type']=='Microsoft.Network/bastionHosts')
        self.assertEqual(bastion['sku']['name'],'Standard')
        self.assertTrue(bastion['properties']['enableTunneling'])
        self.assertFalse(bastion['properties']['enableShareableLink'])
        nsg=next(r for r in self.value['resources'] if r['name']=='ibkrv2stg-admin-nsg')
        inbound=[r['properties'] for r in nsg['properties']['securityRules'] if r['properties']['direction']=='Inbound']
        self.assertEqual([r['sourceAddressPrefix'] for r in inbound if r['access']=='Allow'],['10.50.8.0/26'])
        self.assertTrue(any(r['access']=='Deny' and r['sourceAddressPrefix']=='*' for r in inbound))

if __name__=='__main__':unittest.main()
