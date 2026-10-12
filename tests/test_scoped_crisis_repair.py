import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from repair_sampled_crisis_replay import validate_repair_inputs,digest,SUBJECT,DAY


class ScopedCrisisRepairTests(unittest.TestCase):
    def setUp(self):
        self.data={'subject_id':SUBJECT,'as_of':DAY,'legacy':[
            {'calculation_version':'as-of-holdings-crisis-replay-v2'} for _ in range(121)]}
        self.check={'independent_decimal_verified':True,'exact_holdings_reconciled':True,
                    'maximum_path_difference':1.8e-7,'maximum_drawdown_difference':1.8e-7}

    def test_reviewed_small_difference_passes(self):
        validate_repair_inputs(self.data,self.check,digest(self.data))

    def test_changed_preview_or_scope_rejected(self):
        with self.assertRaises(ValueError):
            validate_repair_inputs(self.data,self.check,'0'*64)
        for field,value in [('subject_id','U16088606'),('as_of','2025-11-26')]:
            data=dict(self.data,**{field:value})
            with self.assertRaises(ValueError):
                validate_repair_inputs(data,self.check,digest(data))

    def test_current_version_or_missing_rows_not_overwritten(self):
        for data in [dict(self.data,legacy=self.data['legacy'][:-1]),copy.deepcopy(self.data)]:
            if len(data['legacy'])==121:
                data['legacy'][0]['calculation_version']='as-of-holdings-crisis-replay-v3'
            with self.assertRaises(ValueError):
                validate_repair_inputs(data,self.check,digest(data))

    def test_unverified_or_large_difference_fails_closed(self):
        for field,value in [('independent_decimal_verified',False),('exact_holdings_reconciled',False),
                            ('maximum_path_difference',0.001),('maximum_drawdown_difference',float('nan'))]:
            check=dict(self.check,**{field:value})
            with self.assertRaises(ValueError):
                validate_repair_inputs(self.data,check,digest(self.data))


if __name__=='__main__':
    unittest.main()
