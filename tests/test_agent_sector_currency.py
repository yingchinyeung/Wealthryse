"""Standalone analysis and collected reports must share dated FX valuations."""
import copy
import unittest
from ibkr_etl.agent_tools import _sector_allocation, AgentToolError
from ibkr_etl.reporting_currency_holdings import normalize_report_holdings


class Cursor:
    def __init__(self, row=None, rows=None): self.row, self.rows = row, rows
    def fetchone(self): return self.row
    def fetchall(self): return self.rows


class Connection:
    def __init__(self):
        self.calls = []
        self.kind = 'physical_account'
        self.currency = 'USD'
        self.source = 'BROKER'
        self.rows = [
            dict(conid='1', symbol='HK', currency='HKD', market_value_text='780', snapshot_date='2026-09-29', listing_exchange='SEHK', sector='Technology', industry='Hardware'),
            dict(conid='2', symbol='TW', currency='TWD', market_value_text='3200', snapshot_date='2026-09-29', listing_exchange='TSE', sector='Financial Services', industry='Bank'),
        ]
        self.fx = [
            dict(from_currency='USD', to_currency='HKD', rate_date='2026-09-29', rate_text='7.8'),
            dict(from_currency='TWD', to_currency='USD', rate_date='2026-09-29', rate_text='0.03125'),
            dict(from_currency='TWD', to_currency='USD', rate_date='2026-09-30', rate_text='0.04'),
        ]
    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if 'MAX(snapshot_date)' in sql: return Cursor(dict(latest='2026-09-30' if 'subject_nav_series' in sql else '2026-09-29'))
        if 'SELECT subject_type, source_account_id' in sql: return Cursor(dict(subject_type=self.kind, source_account_id=self.source))
        if 'SELECT subject_type FROM' in sql: return Cursor(dict(subject_type=self.kind))
        if sql.lstrip().startswith('SELECT COALESCE(reporting_currency'): return Cursor(dict(reporting_currency=self.currency))
        if 'subject_calculation_definitions' in sql: return Cursor(dict(source_account_id='APPROVED_SOURCE'))
        if 'FROM fx_rates' in sql: return Cursor(rows=self.fx)
        if 'FROM synthetic_portfolio_components' in sql: return Cursor(rows=[dict(sector='equity', market_value=230)])
        if 'FROM subject_position_series p' in sql: return Cursor(rows=self.rows)
        raise AssertionError(sql)


class SectorReportingCurrencyTests(unittest.TestCase):
    def run_tool(self, db): return _sector_allocation(db, 'TEST', '2026-09-30', {}).as_dict()
    def test_dated_common_currency_weights_and_native_evidence(self):
        db = Connection(); original = copy.deepcopy(db.rows)
        result = self.run_tool(db); data = result['data']
        self.assertAlmostEqual(data['gross_market_value'], 200)
        self.assertEqual([r['weight'] for r in data['allocation']], [.5, .5])
        self.assertEqual(data['currency'], 'USD')
        self.assertEqual(db.rows, original)
        self.assertEqual(result['citations'][-1]['account_id'], 'BROKER')
        self.assertFalse(data['publication_allowed'])
        self.assertTrue(all(sql.lstrip().startswith('SELECT') for sql, _ in db.calls))
    def test_missing_fx_is_explicit_conflict_not_native_fallback(self):
        db = Connection(); db.fx = []
        with self.assertRaises(AgentToolError) as error: self.run_tool(db)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.code, 'sector_reporting_fx_unavailable')
    def test_unknown_reporting_currency_fails_closed(self):
        db = Connection(); db.currency = None
        with self.assertRaises(AgentToolError) as error: self.run_tool(db)
        self.assertEqual(error.exception.code, 'sector_reporting_currency_unavailable')
    def test_filtered_approved_source_scope(self):
        db = Connection(); db.kind = 'filtered_portfolio'; db.source = None
        self.run_tool(db)
        self.assertEqual(next(p for s,p in db.calls if 'FROM fx_rates' in s), ('APPROVED_SOURCE','2026-09-30'))
    def test_report_collection_recomputes_from_native_holdings_only_once(self):
        db = Connection(); sector = self.run_tool(db)
        report = normalize_report_holdings({'holdings': {'as_of':'2026-09-30','data':{'holdings':db.rows}}, 'sector_allocation':sector}, 'USD', db.fx)
        self.assertEqual(report['sector_allocation']['data'], sector['data'])
        self.assertAlmostEqual(sum(float(r['market_value_text']) for r in report['holdings']['data']['holdings']),200)
    def test_partial_classification_uses_full_reporting_denominator(self):
        db = Connection(); db.rows[1]['sector'] = None
        data = self.run_tool(db)['data']
        self.assertFalse(data['available'])
        self.assertEqual(data['classification_coverage_pct'], 50)
        self.assertEqual(data['allocation'][0]['weight'], .5)
    def test_same_currency_is_identity(self):
        db = Connection(); db.fx = []
        for row in db.rows: row['currency']='USD'; row['market_value_text']='100'
        self.assertEqual(self.run_tool(db)['data']['gross_market_value'],200)
    def test_composite_is_not_reconverted(self):
        db = Connection(); db.kind='composite_portfolio'
        data = self.run_tool(db)['data']
        self.assertEqual(data['gross_market_value'],230)
        self.assertFalse(any('fx_rates' in s for s,_ in db.calls))


if __name__ == '__main__': unittest.main()
