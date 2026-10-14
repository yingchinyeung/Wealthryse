import unittest
from unittest.mock import patch,Mock
from datetime import date,timedelta
from ibkr_etl import crisis_replay as crisis
from ibkr_etl.crisis_identity_policy import VERSION


class ReplayCaptured(Exception):pass


class ProducerIdentityTests(unittest.TestCase):
    def test_approved_identity_precedes_vendor_history_selection(self):
        for ticker in ('PSKY','SKYD'):
            days=[];day=crisis.CRISIS_START
            while day<=crisis.CRISIS_END:
                if day.weekday()<5:days.append(day.isoformat())
                day+=timedelta(days=1)
            symbols=set(crisis.PROXY_BY_SYMBOL.values())|set(crisis.PROXY_BY_CURRENCY.values())|{'SPY'}
            cache={s:{d:100-i/5 for i,d in enumerate(days)} for s in symbols}
            row={'conid':'804144296','symbol':ticker,'description':'Paramount','listing_exchange':'NASDAQ',
                 'currency':'USD','market_value':80.}
            def execute(db,sql,args):
                cursor=Mock()
                if 'SELECT total_text' in sql:cursor.fetchone.return_value={'total_text':'100'}
                elif 'SUM(CAST(p.market_value_text' in sql:cursor.fetchall.return_value=[row]
                else:raise AssertionError('Unexpected database operation')
                return cursor
            captured=[]
            def capture(**kwargs):captured.extend(kwargs['components']);raise ReplayCaptured()
            with patch.object(crisis,'resolve_holdings_as_of',return_value='2026-10-08'), \
                 patch.object(crisis,'_execute',side_effect=execute), \
                 patch.object(crisis,'_fx_rate_as_of',return_value=1.), \
                 patch.object(crisis,'fetch_adjusted_history',side_effect=AssertionError('Direct vendor fetch is forbidden')), \
                 patch.object(crisis,'calculate_fixed_unit_replay',side_effect=capture),self.assertRaises(ReplayCaptured):
                crisis.refresh_crisis_replay(Mock(),account_id='U16088571',holdings_as_of='2026-10-08',price_cache=cache)
            self.assertEqual(len(captured),1)
            self.assertEqual((captured[0].source_symbol,captured[0].mapping_method,captured[0].market_value),('SPY','market_proxy',80.))
            self.assertIn(VERSION,captured[0].proxy_reason)


if __name__=='__main__':unittest.main()
