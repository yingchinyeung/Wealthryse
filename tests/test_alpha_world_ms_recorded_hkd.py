"""Rehearse the real interpreter from a pinned, private source capture.

This verifies recorded observations, not independent certification of IBKR or
market-provider originals. Copies live only in a disposable local database.
"""
from pathlib import Path
import hashlib
import json
import tempfile
import unittest

from ibkr_etl.db import initialize, connect
from ibkr_etl.derived_portfolio import refresh_alpha_world_ms_portfolio, refresh_fixed_unit_composite
from ibkr_etl.subject_onboarding import approved_reference_definitions


class RecordedHkdTests(unittest.TestCase):
    def test_real_interpreter_matches_all_443_pinned_private_navs(self):
        folder=Path(__file__).resolve().parents[1]/'.work/alpha-ms-hkd-hibor-20261007'
        if not (folder/'source-inspection.json').exists():
            self.skipTest('Private captured inputs are not included in the public test fixture')
        raw=(folder/'source-inspection.json').read_bytes()
        expected_raw=(folder/'hkd-history.json').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),'50d294c937fd29ddaa4c678b4eab27528dba815848c0a9ba48a245d4a47bacc4')
        self.assertEqual(hashlib.sha256(expected_raw).hexdigest(),'6dbc9ecfc858094571cd66568f883b6f91bbd69501aa9d09cbfb4f6a208c6661')
        source=json.loads(raw);expected=json.loads(expected_raw)
        with tempfile.TemporaryDirectory(prefix='ms-step2-recorded-') as temp:
            path=Path(temp)/'copied-inputs.sqlite3'
            initialize(path);c=connect(path)
            try:
                c.execute("INSERT INTO ingestion_runs(run_type,started_at,status) VALUES ('private-copied-inputs','2026-10-07','success')")
                run_id=c.execute('SELECT run_id FROM ingestion_runs').fetchone()[0]
                accounts=set(r['account_id'] for r in source['broker_fx'])|{'U16088571','U16088606'}
                for account in accounts:
                    c.execute("INSERT INTO accounts VALUES (?,?, 'USD','fixture','fixture','2025-01-20','2026-09-30','{}')",(account,account))
                for portfolio in ('hong-kong-china','alpha-world-ms'):
                    c.execute("INSERT INTO derived_portfolios VALUES (?,?, 'U16088606','USD','{}','captured-fixture','active','2025-01-20','2026-09-30')",(portfolio,portfolio))
                for row in source['broker_fx']:
                    c.execute('''INSERT INTO fx_rates(account_id,rate_date,from_currency,to_currency,rate_text,row_sha256,last_seen_run_id)
                        VALUES (?,?,?,?,?,'copied-fixture',?)''',tuple(row[k] for k in ('account_id','rate_date','from_currency','to_currency','rate_text'))+(run_id,))
                market={}
                for row in source['components']:
                    day=row['snapshot_date']
                    if day>'2026-09-30':continue
                    if row['component_id']=='U16088571':
                        c.execute('''INSERT INTO nav_snapshots(account_id,snapshot_date,currency,asset_class,total_text,row_sha256,last_seen_run_id)
                            VALUES ('U16088571',?,'USD','TOTAL',?,'copied-fixture',?)''',(day,str(row['source_value']),run_id))
                    elif row['component_id']=='hong-kong-china':
                        c.execute('''INSERT INTO derived_portfolio_nav(portfolio_id,snapshot_date,currency,total_nav,
                            excluded_market_value_base,source_position_count,included_position_count,excluded_position_count,generated_at)
                            VALUES ('hong-kong-china',?,'USD',?,0,0,0,0,'copied-fixture')''',(day,row['source_value']))
                    elif row['component_id'] in ('hang-seng-price-index','spy-settlement'):
                        key=(row['component_id'],row['observation_date'])
                        value=(row['source_currency'],float(row['source_value']))
                        if key in market:self.assertEqual(market[key],value)
                        market[key]=value
                for (instrument,day),(currency,price) in market.items():
                    c.execute("INSERT INTO market_data_prices VALUES (?,?,?,?,'captured-close',?,?,'copied-component-evidence','private-capture','2026-10-07')",
                        (instrument,day,instrument,instrument,currency,price))
                for row in source['nav']:
                    if row['currency']=='USD' and row['snapshot_date']<='2026-09-30':
                        c.execute('''INSERT INTO derived_portfolio_nav(portfolio_id,snapshot_date,currency,total_nav,
                            excluded_market_value_base,source_position_count,included_position_count,excluded_position_count,generated_at)
                            VALUES ('alpha-world-ms',?,'USD',?,0,0,0,0,'captured-legacy')''',(row['snapshot_date'],row['total_nav']))
                c.commit()
                def legacy():return [tuple(r) for r in c.execute("SELECT snapshot_date,total_nav FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-ms' AND currency='USD' ORDER BY snapshot_date")]
                before=legacy()
                result=refresh_alpha_world_ms_portfolio(c)
                self.assertEqual(result['base_currency'],'HKD')
                rows=list(c.execute("SELECT snapshot_date,total_nav FROM derived_portfolio_nav WHERE portfolio_id='alpha-world-ms' AND currency='HKD' ORDER BY snapshot_date"))
                self.assertEqual(len(rows),443)
                self.assertEqual(len(rows),len(expected['series']))
                for actual,wanted in zip(rows,expected['series']):
                    self.assertEqual(actual['snapshot_date'],wanted['date'])
                    self.assertAlmostEqual(actual['total_nav'],wanted['nav'],places=10)
                self.assertEqual(legacy(),before)
                # A valid hash of a USD contract is not authority to run it.
                wrong=approved_reference_definitions()['alpha-world-ms'];wrong['base_currency']='USD'
                with self.assertRaises(ValueError):refresh_fixed_unit_composite(c,wrong)
                self.assertEqual(legacy(),before)
            finally:c.close()


if __name__=='__main__':unittest.main()
