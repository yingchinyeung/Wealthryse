import copy,unittest
from legacy_reporting_reconciliation_20261011 import reconcile_legacy_sector
def fixture():
    old=dict(available=True,holdings=2,classified_holdings=2,gross_market_value=30,classified_market_value=30,classification_coverage_pct=100,provider='stored-provider',license_status='restricted',publication_allowed=False,
        allocation=[dict(sector='Tech',market_value=30,weight=1,holdings=2)])
    p=dict(subject_id='U16088571',reporting_currency='USD',sector_allocation=old,holdings={'holdings':[
        dict(conid='a',currency='USD',snapshot_date='2025-01-31',market_value_text='10'),dict(conid='b',currency='USD',snapshot_date='2025-01-31',market_value_text='-20')]})
    d={**copy.deepcopy(old),'currency':'USD','valuation_method':'dated-reporting-fx-v1','position_date':'2025-01-31','classification_checked_at':'2026-10-08',
        'classifications':[dict(conid='a',market_value=10,weight=1/3,sector='Tech',region='North America',sub_sector='Hardware'),dict(conid='b',market_value=20,weight=2/3,sector='Tech',region='North America',sub_sector='Hardware')],
        'allocation_detail':[dict(region='North America',sector='Tech',sub_sector='Hardware',market_value=30,weight=1,holdings=2)]}
    return p,d
class LegacyReconciliationTests(unittest.TestCase):
    def test_preserves_inputs_and_all_original_sector_fields(self):
        p,d=fixture();original=copy.deepcopy((p,d));result=reconcile_legacy_sector(p,d)
        self.assertEqual((p,d),original)
        for k,v in p['sector_allocation'].items():self.assertEqual(result[k],v)
        self.assertFalse(result['classification_detail_provenance']['reference_is_independent_historical_provider'])
    def reject(self,edit):
        p,d=fixture();edit(p,d)
        with self.assertRaises(ValueError):reconcile_legacy_sector(p,d)
    def test_wrong_account(self):self.reject(lambda p,d:p.update(subject_id='alpha-world-t'))
    def test_mixed_currency(self):self.reject(lambda p,d:p['holdings']['holdings'][0].update(currency='HKD'))
    def test_wrong_date(self):self.reject(lambda p,d:d.update(position_date='2025-02-28'))
    def test_missing_security(self):self.reject(lambda p,d:d['classifications'].pop())
    def test_wrong_security_value(self):self.reject(lambda p,d:d['classifications'][0].update(market_value=11))
    def test_wrong_sector_value(self):self.reject(lambda p,d:d['allocation'][0].update(market_value=31))
    def test_wrong_sector_name(self):self.reject(lambda p,d:d['allocation'][0].update(sector='Finance'))
    def test_wrong_gross(self):self.reject(lambda p,d:p['sector_allocation'].update(gross_market_value=29))
    def test_wrong_count(self):self.reject(lambda p,d:d.update(classified_holdings=1))
    def test_different_provider(self):self.reject(lambda p,d:d.update(provider='other'))
    def test_distribution_boundary(self):self.reject(lambda p,d:d.update(publication_allowed=True))
    def test_nonfinite(self):self.reject(lambda p,d:p['holdings']['holdings'][0].update(market_value_text='NaN'))
    def test_duplicate_security(self):self.reject(lambda p,d:p['holdings']['holdings'][1].update(conid='a'))
    def test_existing_detail_rejected(self):self.reject(lambda p,d:p['sector_allocation'].update(classifications=[]))
    def test_unvalidated_detail_rejected(self):self.reject(lambda p,d:d.update(valuation_method='unknown'))
    def test_detail_totals_rejected(self):self.reject(lambda p,d:d['allocation_detail'][0].update(market_value=29))
    def test_detail_classification_rejected(self):self.reject(lambda p,d:d['classifications'][0].update(sector='Finance'))
    def test_detail_weight_rejected(self):self.reject(lambda p,d:d['classifications'][0].update(weight=0.9))
    def test_rounding_only_tolerated(self):
        p,d=fixture();p['sector_allocation']['gross_market_value']=30.00000000000001
        self.assertEqual(reconcile_legacy_sector(p,d)['gross_market_value'],30.00000000000001)
if __name__=='__main__':unittest.main()
