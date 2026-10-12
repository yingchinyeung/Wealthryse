import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'.work/platform-upgrade-20261009-stage5b/runtime/src')]
from preview_upgrade_private_endpoints_20261009 import validate_changes
from prepare_upgrade_private_endpoints_20261009 import validate_target_baselines,TARGETS,BASE
class Preview(unittest.TestCase):
    def test_exact_create(self):validate_changes([{'resourceId':'/a','changeType':'Create'}],['/a'])
    def test_case_insensitive_id(self):validate_changes([{'resourceId':'/A','changeType':'Create'}],['/a'])
    def test_ignore_unmodified(self):validate_changes([{'resourceId':'/a','changeType':'Create'},{'resourceId':'/unrelated','changeType':'Ignore'}],['/a'])
    def test_modify_rejected(self):
        with self.assertRaises(ValueError):validate_changes([{'resourceId':'/a','changeType':'Modify'}],['/a'])
    def test_delete_rejected(self):
        with self.assertRaises(ValueError):validate_changes([{'resourceId':'/a','changeType':'Delete'}],['/a'])
    def test_foreign_create_rejected(self):
        with self.assertRaises(ValueError):validate_changes([{'resourceId':'/other','changeType':'Create'}],['/a'])
    def test_incomplete_rejected(self):
        with self.assertRaises(ValueError):validate_changes([],['/a'])
    def test_same_name_different_types_preserved(self):
        targets={BASE+kind+'/'+name:{'id':BASE+kind+'/'+name} for _,kind,name,_,_ in TARGETS}
        validate_target_baselines(targets);self.assertEqual(len(targets),7)
    def test_short_name_collision_rejected(self):
        targets={name:{'id':BASE+kind+'/'+name} for _,kind,name,_,_ in TARGETS}
        with self.assertRaises(ValueError):validate_target_baselines(targets)
    def test_wrong_baseline_identity_rejected(self):
        targets={BASE+kind+'/'+name:{'id':BASE+kind+'/'+name} for _,kind,name,_,_ in TARGETS}
        first=next(iter(targets));targets[first]['id']='foreign'
        with self.assertRaises(ValueError):validate_target_baselines(targets)
if __name__=='__main__':unittest.main()
