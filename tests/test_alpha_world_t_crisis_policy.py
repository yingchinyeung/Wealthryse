import copy
import unittest
from ibkr_etl.alpha_world_t_crisis_policy import apply_identity_proxy, VERSION


class TIdentityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.row={'conid':'804144296','holding_symbol':'PSKY','source_symbol':'PSKY',
                  'mapping_method':'direct','current_market_value':100,'opening_weight':.01,
                  'currency':'USD','price_type':'adjusted_close'}
    def test_owner_approved_proxy_and_provenance(self):
        original=copy.deepcopy(self.row)
        result=apply_identity_proxy(self.row,subject_id='alpha-world-t',crisis_start='2008-09-15')
        self.assertEqual(result['source_symbol'],'SPY')
        self.assertEqual(result['mapping_method'],'market_proxy')
        self.assertEqual(result['proxy_policy_version'],VERSION)
        self.assertEqual(result['original_source_symbol'],'PSKY')
        self.assertEqual(result['current_market_value'],100)
        self.assertEqual(result['opening_weight'],.01)
        self.assertEqual(self.row,original)
    def test_same_security_new_ticker(self):
        self.row.update(holding_symbol='SKYD',source_symbol='SKYD')
        result=apply_identity_proxy(self.row,subject_id='alpha-world-t',crisis_start='2008-09-15')
        self.assertEqual(result['source_symbol'],'SPY')
        self.assertEqual(result['original_source_symbol'],'SKYD')
    def test_other_subject_rejected(self):
        for subject in ('alpha-world-ms','U16088571',None):
            with self.subTest(subject=subject),self.assertRaises(ValueError):
                apply_identity_proxy(self.row,subject_id=subject,crisis_start='2008-09-15')
    def test_other_identity_unchanged(self):
        self.row.update(conid='123',holding_symbol='AAPL',source_symbol='AAPL')
        result=apply_identity_proxy(self.row,subject_id='alpha-world-t',crisis_start='2008-09-15')
        self.assertEqual(result,self.row)
        self.assertIsNot(result,self.row)
    def test_identity_conflict_rejected(self):
        self.row['holding_symbol']='AAPL'
        with self.assertRaises(ValueError):
            apply_identity_proxy(self.row,subject_id='alpha-world-t',crisis_start='2008-09-15')
    def test_no_proxy_for_post_listing_stress(self):
        result=apply_identity_proxy(self.row,subject_id='alpha-world-t',crisis_start='2026-01-01')
        self.assertEqual(result,self.row)


if __name__=='__main__':unittest.main()
