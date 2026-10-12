import copy,unittest
import add_upgrade_metadata_private_path_20261010 as m


class PrivatePathTests(unittest.TestCase):
    def test_exact_vault_and_subnet(self):
        p=m.body()['properties']
        self.assertEqual(p['subnet']['id'],m.old.SUBNET)
        self.assertEqual(p['privateLinkServiceConnections'][0]['properties'],{'privateLinkServiceId':m.TARGET,'groupIds':['vault']})

    def test_no_cutover_or_permissions(self):
        self.assertEqual(set(m.body()),{'location','properties'})
        self.assertEqual(set(m.body()['properties']),{'subnet','privateLinkServiceConnections'})
        self.assertEqual(m.body()['location'],'eastasia')

    def test_only_exact_create(self):
        m.validate_changes([{'changeType':'Create','resourceId':m.ENDPOINT}],[m.ENDPOINT])

    def test_update_rejected(self):
        with self.assertRaises(ValueError):m.validate_changes([{'changeType':'Modify','resourceId':m.ENDPOINT}],[m.ENDPOINT])

    def test_delete_rejected(self):
        with self.assertRaises(ValueError):m.validate_changes([{'changeType':'Delete','resourceId':m.ENDPOINT}],[m.ENDPOINT])

    def test_extra_resource_rejected(self):
        with self.assertRaises(ValueError):m.validate_changes([{'changeType':'Create','resourceId':m.ENDPOINT},{'changeType':'Create','resourceId':m.ZONE}],[m.ENDPOINT])

    def test_missing_create_rejected(self):
        with self.assertRaises(ValueError):m.validate_changes([],[m.ENDPOINT])

    def test_wrong_target_rejected(self):
        changed=copy.deepcopy(m.body()['properties'])
        changed['privateLinkServiceConnections'][0]['properties']['privateLinkServiceId']=m.ZONE
        self.assertFalse(m.old.contains_requested(changed,m.body()['properties']))

    def test_extra_connection_rejected(self):
        changed=copy.deepcopy(m.body()['properties'])
        changed['privateLinkServiceConnections']*=2
        self.assertFalse(m.old.contains_requested(changed,m.body()['properties']))


if __name__=='__main__':unittest.main()
