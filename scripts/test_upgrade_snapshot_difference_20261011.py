import sys,types,unittest
sys.modules.setdefault('qa_read_only',types.ModuleType('qa_read_only'))
import probe_upgrade_snapshot_difference_20261011 as c

class DifferenceTests(unittest.TestCase):
    def test_precision_only(self):self.assertEqual(c.changed(100.0,100.0+1e-12,'value'),[('value','numeric_negligible')])
    def test_material(self):self.assertEqual(c.changed(100.0,101.0,'value'),[('value','numeric_material')])
    def test_boolean_not_numeric(self):self.assertEqual(c.changed(True,False,'flag'),[('flag','text_or_boolean')])
    def test_no_values_returned(self):self.assertEqual(c.changed({'a':'private_value_1'},{'a':'private_value_2'}),[('a','text_or_boolean')])
    def test_length_change_not_silently_zipped(self):self.assertEqual(c.changed([1],[1,2],'series'),[('series.length','length')])
    def test_date_set(self):self.assertEqual(c.series_dates({'performance':{'series':[{'date':'2026-08-27','nav':123},{'date':'2026-08-28'}]}}),{'2026-08-27','2026-08-28'})
if __name__=='__main__':unittest.main()
