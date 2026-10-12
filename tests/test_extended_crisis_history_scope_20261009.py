import copy,sys,unittest
from pathlib import Path
from datetime import date,timedelta
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'.work/platform-upgrade-20261009-stage7/runtime/src')]
import apply_extended_crisis_history_20261009 as change

class Scope(unittest.TestCase):
    def setUp(self):
        self.tenant='00000000-0000-0000-0000-000000000001'
        self.package={'qualified':[],'failures':[],'excluded':[{'as_of':'2025-01-20'},{'as_of':'2025-01-21'}]}
        for i in range(446):
            day=(date(2025,1,22)+timedelta(days=i)).isoformat()
            old={'runtime_version':'alpha-world-ms-hkd-risk-runtime-v1','metrics':{'return':.2,'crisis_maximum_drawdown':-.4},'source_snapshot':{'nav':100},'risk_free':.0265}
            payload={**copy.deepcopy(old),'runtime_version':'alpha-world-ms-hkd-risk-runtime-v2'}
            payload['metrics']['crisis_maximum_drawdown']=-.38
            self.package['qualified'].append({'as_of':day,'old_row':{'tenant_id':self.tenant,'subject_id':'alpha-world-ms','as_of_date':day,'analytics_version':'risk-analytics-v2','payload_json':old},'candidate_payload':payload,'candidate_payload_sha256':change.sha(payload)})
    def test_valid(self):self.assertEqual(len(change.validate_scope(self.package,self.tenant)),446)
    def test_scope_and_dates_fail_closed(self):
        for field,value in (('subject_id','alpha-world-t'),('tenant_id','other'),('as_of_date','2025-01-20')):
            package=copy.deepcopy(self.package);package['qualified'][0]['old_row'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):change.validate_scope(package,self.tenant)
        self.package['qualified'][1]=copy.deepcopy(self.package['qualified'][0])
        with self.assertRaises(ValueError):change.validate_scope(self.package,self.tenant)
    def test_actual_metrics_and_inputs_cannot_change_even_with_rehashed_candidate(self):
        for key in ('metrics','source_snapshot','risk_free','runtime_version'):
            package=copy.deepcopy(self.package);row=package['qualified'][0]
            if key=='metrics':row['candidate_payload'][key]['return']=.3
            else:row['candidate_payload'][key]='changed'
            row['candidate_payload_sha256']=change.sha(row['candidate_payload'])
            with self.subTest(key=key),self.assertRaises(ValueError):change.validate_scope(package,self.tenant)
    def test_missing_row_or_wrong_exclusions_rejected(self):
        self.package['qualified'].pop()
        with self.assertRaises(ValueError):change.validate_scope(self.package,self.tenant)

if __name__=='__main__':unittest.main()
