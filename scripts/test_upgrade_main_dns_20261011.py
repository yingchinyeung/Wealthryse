import unittest
from unittest.mock import patch
import control_upgrade_main_dns_20261011 as c

class MainDNSGuards(unittest.TestCase):
    def test_exact_nine_hosts_three_zones(self):
        self.assertEqual(len(c.EXPECTED),9);self.assertEqual(len(c.RECORDS),3)
        self.assertTrue(all(ip.startswith('10.50.10.') for ip in c.EXPECTED.values()))
    def test_registration_disabled(self):
        self.assertEqual(c.body()['properties'],{'registrationEnabled':False,'virtualNetwork':{'id':c.VNET}})
    def test_outside_scope_denied(self):
        with self.assertRaises(ValueError):c.link_id('example.org')
    def test_preexisting_link_denied(self):
        with patch.object(c,'listing',return_value=[{'id':'unrelated'}]),self.assertRaises(ValueError):c.links(False)
    def test_wrong_link_denied(self):
        with patch.object(c,'listing',return_value=[{'id':'unrelated','properties':c.body()['properties']}]),self.assertRaises(ValueError):c.links(True)
    def test_wrong_vnet_denied(self):
        def listing(path,api):return [{'id':path+'/upgrade-main-20261011','properties':{'registrationEnabled':False,'virtualNetwork':{'id':'wrong'}}}]
        with patch.object(c,'listing',side_effect=listing),self.assertRaises(ValueError):c.links(True)
    def test_owned_ready_links(self):
        def listing(path,api):return [{'id':path+'/upgrade-main-20261011','properties':{**c.body()['properties'],'provisioningState':'Succeeded','virtualNetworkLinkState':'Completed'}}]
        with patch.object(c,'listing',side_effect=listing):self.assertEqual(len(c.links(True)),3)
    def test_pending_link_denied(self):
        def listing(path,api):return [{'id':path+'/upgrade-main-20261011','properties':{**c.body()['properties'],'provisioningState':'Updating'}}]
        with patch.object(c,'listing',side_effect=listing),self.assertRaises(ValueError):c.links(True)
    def test_preview_modification_denied(self):
        target=c.link_id(next(iter(c.RECORDS)))
        with self.assertRaises(ValueError):c.old.validate_changes([{'resourceId':target,'changeType':'Modify'}],[target])
    def test_partial_rollback_supported(self):
        first=next(iter(c.RECORDS))
        def listing(path,api):return [{'id':path+'/upgrade-main-20261011','properties':c.body()['properties']}] if first in path else []
        with patch.object(c,'listing',side_effect=listing):self.assertEqual(len(c.rollback_links()),1)
    def test_rollback_ignores_unrelated_links(self):
        with patch.object(c,'listing',return_value=[{'id':'unrelated'}]):self.assertEqual(c.rollback_links(),[])
    def test_rollback_binding_change_denied(self):
        def listing(path,api):return [{'id':path+'/upgrade-main-20261011','properties':{'virtualNetwork':{'id':'wrong'}}}]
        with patch.object(c,'listing',side_effect=listing),self.assertRaises(ValueError):c.rollback_links()

if __name__=='__main__':unittest.main()
