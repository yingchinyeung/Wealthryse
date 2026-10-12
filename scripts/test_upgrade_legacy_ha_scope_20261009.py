import copy,unittest
from qualify_upgrade_legacy_ha_20261009 import guarded_clone,invariant,TARGET,SOURCE

class LegacyHaScopeTests(unittest.TestCase):
    def fixture(self):
        return {'id':TARGET,'location':'East Asia','tags':{'dataClassification':'confidential-financial'},'sku':{'name':'Standard_B1ms','tier':'Burstable'},'properties':{'state':'Ready','availabilityZone':'2','version':'16','network':{'publicNetworkAccess':'Disabled','delegatedSubnetResourceId':'/net/ibkrv11-vnet/subnets/upgrade-recovery'},'highAvailability':{'mode':'Disabled'},'backup':{'backupRetentionDays':35,'earliestRestoreDate':'old'}}}
    def test_only_clone_allowed(self):
        server=self.fixture();guarded_clone(server)
        server['id']=SOURCE
        with self.assertRaises(ValueError):guarded_clone(server)
    def test_region_and_private_guards(self):
        for field,value in [('location','westus'),('tags',{})]:
            server=self.fixture();server[field]=value
            with self.assertRaises(ValueError):guarded_clone(server)
        server=self.fixture();server['properties']['network']['publicNetworkAccess']='Enabled'
        with self.assertRaises(ValueError):guarded_clone(server)
    def test_production_subnet_rejected(self):
        server=self.fixture();server['properties']['network']['delegatedSubnetResourceId']='/net/ibkrv11-vnet/subnets/postgres'
        with self.assertRaises(ValueError):guarded_clone(server)
    def test_configuration_preserved_except_named_mutable_fields(self):
        before=self.fixture();after=copy.deepcopy(before)
        after['sku']={'tier':'GeneralPurpose'};after['properties']['state']='Updating';after['properties']['availabilityZone']='1';after['properties']['highAvailability']={'mode':'ZoneRedundant'};after['properties']['backup']['earliestRestoreDate']='new'
        self.assertEqual(invariant(before),invariant(after))
        for key,val in [('version','17'),('network',{}),('backup',{'backupRetentionDays':7})]:
            changed=copy.deepcopy(after);changed['properties'][key]=val
            self.assertNotEqual(invariant(before),invariant(changed))
        self.assertEqual(before,self.fixture())

if __name__=='__main__':unittest.main()
