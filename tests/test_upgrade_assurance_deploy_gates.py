import sys,unittest,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from deploy_upgrade_assurance_period_20261009 import accept
class Gates(unittest.TestCase):
    def setUp(self):
        self.files={'validator':'hash'}
        self.proof={'passed':True,'image':'candidate','saved_settings_preserved':True,'proof':{'passed':True,'database_writes':0,'model_calls':0,'verified_assurance_files':self.files,'records':[{'subject':s,'checks_failed':0,'status':'pass'} for s in ('alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606')]}}
    def test_complete_pass(self):accept(self.proof,'candidate',self.files)
    def test_wrong_image(self):
        with self.assertRaises(ValueError):accept(self.proof,'other',self.files)
    def test_policy_hash_drift(self):
        with self.assertRaises(ValueError):accept(self.proof,'candidate',{'validator':'other'})
    def test_write_not_allowed_in_rehearsal(self):
        self.proof['proof']['database_writes']=1
        with self.assertRaises(ValueError):accept(self.proof,'candidate',self.files)
    def test_any_failed_subject_blocks_deployment(self):
        self.proof['proof']['records'][0]['checks_failed']=1
        with self.assertRaises(ValueError):accept(self.proof,'candidate',self.files)
    def test_missing_subject_blocks_deployment(self):
        self.proof['proof']['records'].pop()
        with self.assertRaises(ValueError):accept(self.proof,'candidate',self.files)
if __name__=='__main__':unittest.main()
