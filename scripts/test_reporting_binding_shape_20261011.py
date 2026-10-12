import copy,unittest
from scan_reporting_full_nav_20261011 import canonical_cli
def fixture():return {'properties':{'configuration':{'identitySettings':[],'triggerType':'Schedule'},'template':{'containers':[{'imageType':'ContainerImage','image':'same','env':[{'name':'X','secretRef':'original'}]}]}}}
class BindingShapeTests(unittest.TestCase):
    def test_only_reviewed_defaults_removed(self):
        p=fixture();old=copy.deepcopy(p);new=canonical_cli(p)
        self.assertEqual(p,old);self.assertEqual(new['properties']['configuration'],{'triggerType':'Schedule'})
        self.assertEqual(new['properties']['template']['containers'][0],{'image':'same','env':[{'name':'X','secretRef':'original'}]})
    def test_nonempty_identity_rejected(self):
        p=fixture();p['properties']['configuration']['identitySettings']=[{'identity':'unexpected'}]
        with self.assertRaises(ValueError):canonical_cli(p)
    def test_different_image_type_rejected(self):
        p=fixture();p['properties']['template']['containers'][0]['imageType']='Other'
        with self.assertRaises(ValueError):canonical_cli(p)
    def test_missing_defaults_rejected(self):
        p=fixture();del p['properties']['configuration']['identitySettings']
        with self.assertRaises(ValueError):canonical_cli(p)
if __name__=='__main__':unittest.main()
