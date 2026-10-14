import unittest
from unittest.mock import Mock,patch
from ibkr_etl.risk_refresh import inception_without_comparisons

class InceptionBoundary(unittest.TestCase):
    def check(self,day,rows,contract_error=None):
        c=Mock();c.execute.return_value.fetchall.return_value=rows
        with patch('ibkr_etl.subject_read_contract.require_ms_hkd_contract',side_effect=contract_error):
            result=inception_without_comparisons(c,tenant_id='tenant',subject_id='alpha-world-ms',as_of=day)
        c.commit.assert_not_called()
        if c.execute.called:
            query,args=c.execute.call_args.args
            self.assertIn("currency='HKD'",query)
            self.assertEqual(args,('tenant','alpha-world-ms',day))
            self.assertTrue(query.strip().startswith('SELECT'))
        return result
    def rows(self):return [{'snapshot_date':'2025-01-20','total_text':'100'},{'snapshot_date':'2025-01-21','total_text':'101'}]
    def test_first_day_unavailable(self):self.assertTrue(self.check('2025-01-20',self.rows()[:1]))
    def test_second_day_unavailable(self):self.assertTrue(self.check('2025-01-21',self.rows()))
    def test_later_missing_observation_still_blocks(self):self.assertFalse(self.check('2025-01-22',self.rows()))
    def test_missing_first_or_second_day_blocks(self):
        for rows in ([],self.rows()[:1],self.rows()[1:]):self.assertFalse(self.check('2025-01-21',rows))
    def test_duplicate_and_earlier_history_block(self):
        for rows in (self.rows()*2,[{'snapshot_date':'2025-01-19','total_text':'100'}]+self.rows()):self.assertFalse(self.check('2025-01-21',rows))
    def test_invalid_nav_blocks(self):
        for v in ('NaN','Infinity','0','-1','invalid',None,True):
            rows=self.rows();rows[1]['total_text']=v
            with self.subTest(v=v):self.assertFalse(self.check('2025-01-21',rows))
    def test_contract_drift_blocks(self):self.assertFalse(self.check('2025-01-21',self.rows(),ValueError('changed contract')))
    def test_invalid_or_outside_dates_block(self):
        for day in ('2025-01-19','2026-10-08','garbage'):self.assertFalse(self.check(day,self.rows()))

if __name__=='__main__':unittest.main()
