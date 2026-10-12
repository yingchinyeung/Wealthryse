import copy,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'.work/platform-upgrade-20261009-stage7/runtime/src')]
from deploy_extended_crisis_identity_20261009 import patch_for

class ImageOnly(unittest.TestCase):
    def setUp(self):
        self.live={'identity':{'type':'UserAssigned'},'properties':{'configuration':{'schedule':'unchanged'},
            'template':{'revisionSuffix':'old-revision','containers':[{'image':'old@sha256:123',
                'command':['python'],'args':['scripts/build_risk_snapshots.py'],
                'env':[{'name':'DATABASE_URL','secretRef':'database-url'},{'name':'MODE','value':'private'}],
                'resources':{'cpu':1,'memory':'2Gi'}}]}}}
    def test_only_template_image_changes_for_worker(self):
        before=copy.deepcopy(self.live);result=patch_for(self.live,'new@sha256:456')
        expected=copy.deepcopy(self.live['properties']['template']);expected['containers'][0]['image']='new@sha256:456'
        self.assertEqual(result,{'properties':{'template':expected}});self.assertEqual(before,self.live)
    def test_web_changes_only_image_and_revision(self):
        result=patch_for(self.live,'new@sha256:456',web=True)['properties']['template']
        result['containers'][0]['image']=self.live['properties']['template']['containers'][0]['image']
        result['revisionSuffix']='old-revision'
        self.assertEqual(result,self.live['properties']['template'])
    def test_rollback_roundtrip(self):
        trial=copy.deepcopy(self.live);trial['properties']['template']=patch_for(self.live,'new@sha256:456')['properties']['template']
        restored=patch_for(trial,'old@sha256:123')['properties']['template']
        self.assertEqual(restored,self.live['properties']['template'])
    def test_additional_containers_rejected(self):
        self.live['properties']['template']['containers'].append({'image':'unexpected'})
        with self.assertRaises(ValueError):patch_for(self.live,'new')
    def test_worker_null_defaults_are_preserved(self):
        self.live['properties']['template']['initContainers']=None
        self.live['properties']['template']['containers'][0]['probes']=None
        actual=patch_for(self.live,'new')['properties']['template']
        actual['containers'][0]['image']='old@sha256:123'
        self.assertEqual(actual,self.live['properties']['template'])

if __name__=='__main__':unittest.main()
