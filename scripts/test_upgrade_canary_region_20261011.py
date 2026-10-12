import copy,unittest
import control_upgrade_private_canaries_v2_20261011 as c

class RegionGuard(unittest.TestCase):
    def test_observed_display_name(self):c.same({'location':'East Asia'},{'location':'eastasia'})
    def test_canonical_name(self):c.same({'location':'eastasia'},{'location':'eastasia'})
    def test_other_region_rejected(self):
        with self.assertRaises(ValueError):c.same({'location':'southeastasia'},{'location':'eastasia'})
    def test_other_changes_not_waived(self):
        with self.assertRaises(ValueError):c.same({'location':'East Asia','identity':'changed'},{'location':'eastasia','identity':'original'})
    def test_original_not_modified(self):
        actual={'location':'East Asia'};before=copy.deepcopy(actual);c.same(actual,{'location':'eastasia'});self.assertEqual(actual,before)

if __name__=='__main__':unittest.main()
