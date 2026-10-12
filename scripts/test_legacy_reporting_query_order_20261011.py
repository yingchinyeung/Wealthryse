import unittest
from legacy_reporting_query_order_20261011 import stable_legacy_sector_query as stable
QUERY="SELECT p.conid FROM subject_position_series p WHERE p.subject_id = %s AND p.snapshot_date = %s ORDER BY ABS(COALESCE(NULLIF(p.market_value_text, ''), '0')::numeric) DESC\n"
class StableOrderTests(unittest.TestCase):
    def test_exact_query(self):self.assertTrue(stable(QUERY,('provider','U16088571','2025-06-30')).endswith('DESC, p.conid ASC'))
    def test_other_select_unchanged(self):self.assertEqual(stable('SELECT rate_text FROM fx_rates',()),'SELECT rate_text FROM fx_rates')
    def test_other_subject_rejected(self):
        with self.assertRaises(ValueError):stable(QUERY,('provider','U16088606','2025-06-30'))
    def test_unexpected_order_rejected(self):
        with self.assertRaises(ValueError):stable(QUERY.replace('DESC','ASC'),('provider','U16088571','2025-06-30'))
    def test_existing_tie_break_rejected(self):
        with self.assertRaises(ValueError):stable(QUERY.rstrip()+', p.conid DESC',('provider','U16088571','2025-06-30'))
    def test_missing_date_constraint_rejected(self):
        with self.assertRaises(ValueError):stable(QUERY.replace(' AND p.snapshot_date = %s',''),('provider','U16088571','2025-06-30'))
if __name__=='__main__':unittest.main()
