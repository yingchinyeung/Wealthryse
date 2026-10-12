import copy,sys,unittest
from pathlib import Path
from datetime import date,timedelta
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'.work/platform-upgrade-20261009-stage5b/runtime/src')]
import apply_ms_history_extension_20261009 as change
class Scope(unittest.TestCase):
    def setUp(self):
        self.tenant='00000000-0000-0000-0000-000000000001';self.package={'qualified':[],'failures':[{'as_of':'2025-01-20'},{'as_of':'2025-01-21'}]}
        for i in range(445):
            day=(date(2025,1,22)+timedelta(days=i)).isoformat();payload={'metrics':{'return':.2,'extension':.3}}
            self.package['qualified'].append({'as_of':day,'old_row':{'tenant_id':self.tenant,'subject_id':'alpha-world-ms','as_of_date':day,'analytics_version':'risk-analytics-v2','payload_json':{'metrics':{'return':.2}}},'candidate_payload':payload,'candidate_payload_sha256':change.sha(payload)})
    def test_valid(self):self.assertEqual(len(change.validate_scope(self.package,self.tenant)),445)
    def test_wrong_tenant(self):
        with self.assertRaises(ValueError):change.validate_scope(self.package,'foreign')
    def test_wrong_subject(self):
        self.package['qualified'][0]['old_row']['subject_id']='alpha-world-t'
        with self.assertRaises(ValueError):change.validate_scope(self.package,self.tenant)
    def test_duplicate_date(self):
        self.package['qualified'][1]=copy.deepcopy(self.package['qualified'][0])
        with self.assertRaises(ValueError):change.validate_scope(self.package,self.tenant)
    def test_inception_date_excluded(self):
        row=self.package['qualified'][0];row['as_of']=row['old_row']['as_of_date']='2025-01-20'
        with self.assertRaises(ValueError):change.validate_scope(self.package,self.tenant)
    def test_candidate_tampering(self):
        self.package['qualified'][0]['candidate_payload']['metrics']['extension']=99
        with self.assertRaises(ValueError):change.validate_scope(self.package,self.tenant)
    def test_core_change_even_with_new_hash(self):
        row=self.package['qualified'][0];row['candidate_payload']['metrics']['return']=.4;row['candidate_payload_sha256']=change.sha(row['candidate_payload'])
        with self.assertRaises(ValueError):change.validate_scope(self.package,self.tenant)
if __name__=='__main__':unittest.main()
