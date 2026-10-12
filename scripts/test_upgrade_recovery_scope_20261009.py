import unittest
from upgrade_fresh_recovery_20261009 import validate_network,configuration,vnet_boundary

def network(prefix='10.50.4.0/28'):
    return {'properties':{'addressSpace':{'addressPrefixes':['10.50.0.0/16']},'subnets':[{'name':'postgres','properties':{'addressPrefix':prefix,'delegations':[{'etag':'old','properties':{'serviceName':'Microsoft.DBforPostgreSQL/flexibleServers'}}]}}]}}

class ScopeTests(unittest.TestCase):
    def test_dedicated_subnet_allowed(self):validate_network(network())
    def test_overlap_rejected(self):
        with self.assertRaises(ValueError):validate_network(network('10.50.11.0/24'))
    def test_outside_vnet_rejected(self):
        value=network();value['properties']['addressSpace']['addressPrefixes']=['10.40.0.0/16']
        with self.assertRaises(ValueError):validate_network(value)
    def test_only_etag_ignored(self):
        left=network();right=network();right['properties']['subnets'][0]['properties']['delegations'][0]['etag']='new'
        self.assertEqual(configuration(vnet_boundary(left)),configuration(vnet_boundary(right)))
    def test_delegation_drift_rejected(self):
        left=network();right=network();right['properties']['subnets'][0]['properties']['delegations'][0]['properties']['serviceName']='Other'
        self.assertNotEqual(configuration(vnet_boundary(left)),configuration(vnet_boundary(right)))
    def test_route_drift_preserved(self):
        left=network();right=network();right['properties']['subnets'][0]['properties']['routeTable']={'id':'other'}
        self.assertNotEqual(configuration(vnet_boundary(left)),configuration(vnet_boundary(right)))
if __name__=='__main__':unittest.main()
