import importlib.util,sys,types,unittest
from pathlib import Path
from unittest.mock import patch
class PriceTests(unittest.TestCase):
    def setUp(self):
        spec=importlib.util.spec_from_file_location('lineage_tested',Path(__file__).with_name('probe_upgrade_input_lineage_20261011.py'));self.m=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'qa_read_only':types.ModuleType('qa_read_only')}):spec.loader.exec_module(self.m)
        self.a=[{'symbol':'X','price_date':d,'price':p,'provider':'p','currency':'USD','price_type':'adjusted_close','source':'s'} for d,p in [('2008-09-15',10),('2008-09-16',11)]]
    def test_same(self):self.assertEqual(self.m.price_changes(self.a,self.a),[])
    def test_uniform_adjustment(self):
        b=[{**r,'price':r['price']*2} for r in self.a];self.assertEqual(self.m.price_changes(self.a,b)[0]['maximum_normalized_path_change'],0)
    def test_different_path(self):
        b=[self.a[0],{**self.a[1],'price':12}];self.assertGreater(self.m.price_changes(self.a,b)[0]['maximum_normalized_path_change'],.09)
    def test_duplicates(self):
        with self.assertRaises(ValueError):self.m.price_changes(self.a+self.a,self.a)
    def test_nonpositive(self):
        with self.assertRaises(ValueError):self.m.price_changes(self.a,[{**r,'price':0} for r in self.a])
    def test_metadata(self):
        b=[{**r,'price':r['price']*2,'provider':'other'} for r in self.a];self.assertFalse(self.m.price_changes(self.a,b)[0]['provider_currency_type_source_unchanged'])
if __name__=='__main__':unittest.main()
