"""Offline scope checks for the frozen, unlinked DNS deployment."""
import copy,json,unittest
from pathlib import Path
from prepare_upgrade_scoped_dns_20261009 import compose,resource_id,BASE

class ScopedDnsTests(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10/unlinked-dns-v2/plan.json'
        self.plan=json.loads(path.read_bytes())

    def test_exact_frozen_template_and_scope(self):
        zones,template=compose(self.plan['endpoints'])
        self.assertEqual(zones,self.plan['zones'])
        self.assertEqual(template,self.plan['template'])
        ids=[resource_id(r) for r in template['resources']]
        self.assertEqual(ids,self.plan['expected_ids'])
        self.assertEqual(len(ids),17)
        self.assertEqual(len(set(ids)),17)
        self.assertTrue(all(i.startswith(BASE) for i in ids))

    def test_no_live_network_links_or_service_mutations(self):
        _,template=compose(self.plan['endpoints'])
        kinds={r['type'] for r in template['resources']}
        self.assertEqual(kinds,{'Microsoft.Network/privateDnsZones','Microsoft.Network/privateDnsZones/A','Microsoft.Network/privateEndpoints/privateDnsZoneGroups'})

    def test_legacy_exact_child_zones_are_disjoint(self):
        zones,_=compose(self.plan['endpoints'])
        legacy=[(n,z) for n,z in zones.items() if z['network']=='legacy']
        self.assertEqual(len(legacy),4)
        self.assertTrue(all(set(z['records'])=={'@'} for _,z in legacy))
        self.assertTrue(all(ip.startswith('10.40.10.') for _,z in legacy for ip in z['records']['@']))
        self.assertTrue(all(ip.startswith('10.50.10.') for z in zones.values() if z['network']=='main' for ips in z['records'].values() for ip in ips))

    def test_main_associations_only(self):
        _,template=compose(self.plan['endpoints'])
        groups=[r for r in template['resources'] if r['type'].endswith('/privateDnsZoneGroups')]
        self.assertEqual(len(groups),6)
        for group in groups:
            self.assertEqual(self.plan['endpoints'][group['name'].split('/')[0]]['network'],'main')

    def test_duplicate_record_rejected(self):
        endpoints=copy.deepcopy(self.plan['endpoints'])
        endpoints['duplicate']=copy.deepcopy(next(iter(endpoints.values())))
        with self.assertRaises(ValueError):compose(endpoints)

    def test_resource_hierarchy_rejected(self):
        with self.assertRaises(ValueError):resource_id({'type':'Microsoft.Network/privateDnsZones/A','name':'missing-child'})

if __name__=='__main__':unittest.main()
