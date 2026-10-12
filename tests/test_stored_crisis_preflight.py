import copy
from datetime import date,timedelta
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from inspect_crisis_gap import verify_stored_replay


class StoredCrisisPreflightTests(unittest.TestCase):
    def setUp(self):
        self.holdings=[{'conid':'1','currency':'USD','market_value':50}]
        self.mappings=[{'conid':'1','currency':'USD','current_market_value':50,'opening_weight':0.5,
                       'holding_symbol':'TEST','description':'Test','source_symbol':'SPY',
                       'mapping_method':'market_proxy','proxy_reason':'test','price_type':'adjusted_close'}]
        days=[(date(2008,9,15)+timedelta(days=i)).isoformat() for i in range(120)]+['2009-03-09']
        self.prices=[{'symbol':'SPY','currency':'USD','price_type':'adjusted_close','price_date':d,'price':100}
                     for d in days]
        self.legacy=[{'observation_date':d,'normalized_value':1,'drawdown':0} for d in days]

    def verify(self,holdings=None,mappings=None,prices=None,legacy=None):
        return verify_stored_replay(self.holdings if holdings is None else holdings,
              self.mappings if mappings is None else mappings,100,
              self.prices if prices is None else prices,self.legacy if legacy is None else legacy)

    def test_exact_mapping_recomputed_without_writes(self):
        result=self.verify()
        self.assertTrue(result['legacy_values_match'])
        self.assertTrue(result['independent_decimal_verified'])
        self.assertFalse(result['database_changed'])

    def test_independent_calculation_catches_incorrect_engine_output(self):
        bad={'daily':[{'observation_date':row['observation_date'],'normalized_value':0.8,'drawdown':-0.2}
                      for row in self.legacy],'maximum_drawdown':-0.2}
        with patch('inspect_crisis_gap.calculate_fixed_unit_replay',return_value=bad):
            with self.assertRaisesRegex(ValueError,'Independent decimal'):
                self.verify()

    def test_negative_cash_leverage_checked_independently(self):
        holdings=copy.deepcopy(self.holdings)
        holdings[0]['market_value']=150
        mappings=copy.deepcopy(self.mappings)
        mappings[0].update(current_market_value=150,opening_weight=1.5)
        prices=copy.deepcopy(self.prices)
        prices[-1]['price']=80
        result=self.verify(holdings=holdings,mappings=mappings,prices=prices)
        self.assertAlmostEqual(result['recomputed_maximum_drawdown'],-0.3)
        self.assertTrue(result['independent_decimal_verified'])

    def test_stale_or_cross_currency_mapping_fails(self):
        for key,value in [('current_market_value',49),('opening_weight',0.6),('currency','HKD'),('conid','2')]:
            rows=copy.deepcopy(self.mappings)
            rows[0][key]=value
            with self.assertRaises(ValueError):
                self.verify(mappings=rows)

    def test_incomplete_or_bad_prices_fail(self):
        with self.assertRaises(ValueError):
            self.verify(prices=self.prices[:-1])
        for value in (0,-1,float('nan'),float('inf')):
            rows=copy.deepcopy(self.prices)
            rows[0]['price']=value
            with self.assertRaises(ValueError):
                self.verify(prices=rows)
        with self.assertRaises(ValueError):
            self.verify(prices=self.prices+[self.prices[0]])

    def test_legacy_difference_is_not_silently_certified(self):
        rows=copy.deepcopy(self.legacy)
        rows[-1]['normalized_value']=0.99
        self.assertFalse(self.verify(legacy=rows)['legacy_values_match'])
        with self.assertRaises(ValueError):
            self.verify(legacy=rows[:-1])


if __name__=='__main__':
    unittest.main()
