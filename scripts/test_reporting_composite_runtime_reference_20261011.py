import copy
import unittest
from reporting_composite_runtime_reference_20261011 import validate, composite_branch, classification_order_only


class Tests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'component_type':'long_account','contribution':'91'}, {'component_type':'long_account','contribution':'45'}, {'component_type':'short_index','contribution':'-49'}, {'component_type':'cash','contribution':'58'}]
        self.actual = {'classification_basis':'composite sleeve type', 'allocation':[{'sector':k,'market_value':v,'weight':v/243} for k,v in [('Long Account',136),('Short Index',49),('Cash',58)]], 'gross_market_value':243,'classified_market_value':243,'holdings':3,'classified_holdings':3,'classification_coverage_pct':100}

    def test_valid(self):
        self.assertEqual(validate(self.actual,self.rows)['sleeves'],3)

    def test_changed_weight(self):
        self.actual['allocation'][0]['weight'] += .001
        with self.assertRaises(ValueError): validate(self.actual,self.rows)

    def test_changed_gross(self):
        self.actual['gross_market_value'] = 244
        with self.assertRaises(ValueError): validate(self.actual,self.rows)

    def test_duplicate(self):
        self.actual['allocation'][2] = copy.deepcopy(self.actual['allocation'][1])
        with self.assertRaises(ValueError): validate(self.actual,self.rows)

    def test_nonfinite(self):
        self.rows[0]['contribution'] = 'NaN'
        with self.assertRaises(ValueError): validate(self.actual,self.rows)

    def test_missing_coverage(self):
        self.actual['classification_coverage_pct'] = 99
        with self.assertRaises(ValueError): validate(self.actual,self.rows)

    def test_branch_detects_changes(self):
        code = "def _sector_allocation():\n    if subject == 'composite_portfolio':\n        return 1\n"
        self.assertNotEqual(composite_branch(code),composite_branch(code.replace('return 1','return 2')))

    def test_empty(self):
        with self.assertRaises(ValueError): validate(self.actual,[])

    def test_classification_order(self):
        rows=[{'conid':'1','weight':.4},{'conid':'2','weight':.6}]
        self.assertTrue(classification_order_only(rows,list(reversed(rows))))
        self.assertFalse(classification_order_only(rows,rows))

    def test_classification_amount(self):
        with self.assertRaises(ValueError): classification_order_only([{'conid':'1','weight':.4}],[{'conid':'1','weight':.5}])

    def test_classification_duplicate(self):
        with self.assertRaises(ValueError): classification_order_only([{'conid':'1'},{'conid':'1'}],[{'conid':'1'}])

    def test_classification_metadata(self):
        with self.assertRaises(ValueError): classification_order_only([{'conid':'1','sector':'Tech'}],[{'conid':'1','sector':'Other'}])


if __name__ == '__main__': unittest.main()
