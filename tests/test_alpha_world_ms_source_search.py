"""Synthetic tests for exact-account/date archive searches; no Azure calls."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ms_archive_search', ROOT / 'scripts/search_alpha_world_ms_azure_sources.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SourceSearchTests(unittest.TestCase):
    def test_multiday_nav_does_not_imply_position_on_every_day(self):
        xml = b'''<FlexQueryResponse><FlexStatements><FlexStatement accountId="U16088571" fromDate="20260827" toDate="20260902"><OpenPositions><OpenPosition conid="1" position="2" /></OpenPositions><EquitySummaryInBase><EquitySummaryByReportDateInBase reportDate="20260827" total="100" /></EquitySummaryInBase></FlexStatement></FlexStatements></FlexQueryResponse>'''
        result = module.inspect_xml(xml)
        self.assertEqual(result['target_position_count'], 0)
        self.assertEqual(result['target_nav_count'], 1)
        self.assertEqual(result['position_dates'], [('U16088571', '2026-09-02')])

    def test_one_day_positions_and_account_filter(self):
        xml = b'''<FlexQueryResponse><FlexStatements><FlexStatement accountId="U16088571" fromDate="20260827" toDate="20260827"><OpenPositions><OpenPosition conid="1" position="2" /></OpenPositions></FlexStatement><FlexStatement accountId="OTHER" toDate="20260827"><OpenPositions><OpenPosition conid="1" position="2" /></OpenPositions></FlexStatement></FlexStatements></FlexQueryResponse>'''
        self.assertEqual(module.inspect_xml(xml)['target_position_count'], 1)

    def test_readonly_memory_database(self):
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE position_snapshots(account_id TEXT,snapshot_date TEXT,quantity_text TEXT)')
            db.executemany('INSERT INTO position_snapshots VALUES(?,?,?)',
                          [('U16088606','2026-08-28','3'),('OTHER','2026-08-28','4')])
            raw = db.serialize()
        result = module.inspect_sqlite(raw)
        self.assertEqual(result['target_position_count'], 1)
        self.assertEqual(result['schema_errors'], {})

    def test_csv_openposition_only(self):
        result = module.inspect_csv(b'account_id,report_date,record_type\nU16088571,2026-08-27,OpenPosition\nU16088571,2026-08-27,Trade\n', 'flex_rows.csv')
        self.assertEqual(result['target_position_count'], 1)

    def test_nested_json_context_not_nav(self):
        data = {'account_id':'U16088606','snapshot_date':'2026-08-28',
                'nav':{'value':100},'positions':[{'quantity_text':'2','conid':'3'}]}
        self.assertEqual(len(module.inspect_json(data)), 1)


if __name__ == '__main__':
    unittest.main()
