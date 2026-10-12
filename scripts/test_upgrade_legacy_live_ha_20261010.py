import copy
import unittest
from deploy_upgrade_legacy_ha_20261010 import guard, invariant, mutation_body, TARGET, SKU, ORIGINAL, rid


class ScopeTests(unittest.TestCase):
    def fixture(self):
        return {'id': TARGET, 'location': 'eastasia', 'sku': dict(ORIGINAL), 'properties': {'state': 'Ready', 'availabilityZone': '3', 'highAvailability': {'mode': 'Disabled'}, 'version': '16', 'minorVersion': '14', 'network': {'publicNetworkAccess': 'Disabled', 'delegatedSubnetResourceId': rid('Microsoft.Network/virtualNetworks/subnets', 'ibkrv11-vnet/postgres')}, 'backup': {'backupRetentionDays': 35, 'geoRedundantBackup': 'Disabled', 'earliestRestoreDate': 'old'}}}

    def test_exact_scope(self):
        s = self.fixture(); guard(s)
        for key, value in [('id', TARGET + '-clone'), ('location', 'westus')]:
            bad = copy.deepcopy(s); bad[key] = value
            with self.assertRaises(ValueError): guard(bad)

    def test_network_backup_guards(self):
        for path, key, value in [('network', 'publicNetworkAccess', 'Enabled'), ('network', 'delegatedSubnetResourceId', 'other'), ('backup', 'backupRetentionDays', 7), ('backup', 'geoRedundantBackup', 'Enabled')]:
            bad = self.fixture(); bad['properties'][path][key] = value
            with self.assertRaises(ValueError): guard(bad)

    def test_only_named_changes(self):
        s = self.fixture(); changed = copy.deepcopy(s)
        changed['sku'] = SKU; changed['properties']['state'] = 'Updating'
        changed['properties']['highAvailability'] = {'mode': 'ZoneRedundant'}
        changed['properties']['availabilityZone'] = '1'
        changed['properties']['backup']['earliestRestoreDate'] = 'new'
        self.assertEqual(invariant(s), invariant(changed))
        for key, value in [('minorVersion', '15'), ('version', '17'), ('network', {}), ('authentication', {'mode': 'other'})]:
            bad = copy.deepcopy(changed); bad['properties'][key] = value
            self.assertNotEqual(invariant(s), invariant(bad))

    def test_exact_scale_payload(self):
        self.assertEqual(mutation_body('scale', self.fixture(), {}), {'sku': SKU})

    def test_enable_preserves_primary(self):
        s = self.fixture(); s['sku'] = SKU
        plan = {'primary_zone': '3', 'standby_zone': '1'}
        self.assertEqual(mutation_body('enable', s, plan), {'properties': {'availabilityZone': '3', 'highAvailability': {'mode': 'ZoneRedundant', 'standbyAvailabilityZone': '1'}}})
        s['properties']['availabilityZone'] = '2'
        with self.assertRaises(ValueError): mutation_body('enable', s, plan)

    def test_inflight_and_repeat_rejected(self):
        s = self.fixture(); s['properties']['state'] = 'Updating'
        with self.assertRaises(ValueError): mutation_body('scale', s, {})
        s = self.fixture(); s['sku'] = SKU
        with self.assertRaises(ValueError): mutation_body('scale', s, {})
        with self.assertRaises(ValueError): mutation_body('other', s, {})


if __name__ == '__main__': unittest.main()
