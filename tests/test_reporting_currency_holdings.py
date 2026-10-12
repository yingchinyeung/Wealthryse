import copy
import unittest
from unittest.mock import MagicMock
from ibkr_etl.reporting_currency_holdings import normalize_report_holdings, normalize_collected_report_holdings


def inputs():
    return {'holdings': {'as_of':'2026-09-30','data':{'holdings':[
        {'conid':'a','currency':'HKD','snapshot_date':'2026-09-30','market_value_text':'780','mark_price_text':'7.8'},
        {'conid':'b','currency':'TWD','snapshot_date':'2026-09-30','market_value_text':'3200','mark_price_text':'32'}]}},
        'sector_allocation': {'data': {'available':True,'classifications':[
            {'conid':'a','sector':'Tech','region':'China','sub_sector':'Hardware','market_value':780,'weight':0},
            {'conid':'b','sector':'Finance','region':'Taiwan','sub_sector':'Bank','market_value':3200,'weight':0}]}}}


def fx():
    return [{'rate_date':'2026-09-30','from_currency':'USD','to_currency':'HKD','rate_text':'7.8'},
            {'rate_date':'2026-09-30','from_currency':'TWD','to_currency':'USD','rate_text':'0.03125'}]


class ReportingHoldingsCurrencyTests(unittest.TestCase):
    def run_model(self,data=None,rates=None):return normalize_report_holdings(inputs() if data is None else data,'USD',fx() if rates is None else rates)
    def test_common_currency_before_sector_weights(self):
        p=self.run_model();s=p['sector_allocation']['data']
        self.assertAlmostEqual(s['gross_market_value'],200)
        self.assertEqual([r['weight'] for r in s['allocation']],[.5,.5])
        self.assertEqual(s['currency'],'USD')
    def test_native_evidence_and_inputs_preserved(self):
        data=inputs();before=copy.deepcopy(data);p=self.run_model(data)
        self.assertEqual(data,before)
        a=next(r for r in p['holdings']['data']['holdings'] if r['conid']=='a')
        self.assertEqual(a['native_market_value_text'],'780');self.assertEqual(a['native_currency'],'HKD')
        self.assertAlmostEqual(float(a['market_value_text']),100);self.assertAlmostEqual(float(a['mark_price_text']),1)
        self.assertEqual(a['reporting_fx']['direction'],'reciprocal')
    def test_missing_fx_rejected(self):
        with self.assertRaisesRegex(ValueError,'Missing dated'):self.run_model(rates=[])
    def test_future_fx_not_used(self):
        rates=fx();rates[0]['rate_date']='2026-10-01'
        with self.assertRaises(ValueError):self.run_model(rates=rates)
    def test_invalid_latest_not_hidden_by_prior(self):
        rates=fx()+[dict(fx()[1],rate_date='2026-09-29')];rates[1]['rate_text']='0'
        with self.assertRaises(ValueError):self.run_model(rates=rates)
    def test_ambiguous_fx_rejected(self):
        with self.assertRaises(ValueError):self.run_model(rates=fx()+[fx()[1]])
    def test_negative_holdings_keep_signed_value_and_absolute_weight(self):
        data=inputs();data['holdings']['data']['holdings'][0]['market_value_text']='-780'
        p=self.run_model(data);self.assertAlmostEqual(p['sector_allocation']['data']['gross_market_value'],200)
        self.assertLess(float(next(r for r in p['holdings']['data']['holdings'] if r['conid']=='a')['market_value_text']),0)
    def test_duplicate_holdings_rejected(self):
        data=inputs();data['holdings']['data']['holdings'].append(copy.deepcopy(data['holdings']['data']['holdings'][0]))
        with self.assertRaises(ValueError):self.run_model(data)
    def test_classification_must_cover_available_holdings(self):
        data=inputs();data['sector_allocation']['data']['classifications'].pop()
        with self.assertRaises(ValueError):self.run_model(data)
    def test_nonfinite_values_rejected(self):
        data=inputs();data['holdings']['data']['holdings'][0]['market_value_text']='NaN'
        with self.assertRaises(ValueError):self.run_model(data)
    def test_double_normalization_rejected(self):
        with self.assertRaises(ValueError):self.run_model(self.run_model())
    def test_composite_values_not_reconverted(self):
        data=inputs();data['sector_allocation']['data']['classification_basis']='composite sleeve type'
        for r in data['holdings']['data']['holdings']:r['currency']='USD'
        self.assertEqual(self.run_model(data),data)
    def test_composite_currency_mismatch_rejected(self):
        data=inputs();data['sector_allocation']['data']['classification_basis']='composite sleeve type'
        with self.assertRaises(ValueError):self.run_model(data)
    def test_account_scoped_fx_read_and_citations(self):
        db=MagicMock();db.execute.return_value.fetchone.return_value={'subject_type':'filtered_portfolio','source_account_id':'SOURCE'}
        db.execute.return_value.fetchall.return_value=fx()
        p=normalize_collected_report_holdings(db,'FILTERED','USD',inputs())
        self.assertEqual(db.execute.call_args.args[1],('SOURCE','2026-09-30'))
        self.assertTrue(all(c.args[0].lstrip().startswith('SELECT') for c in db.execute.call_args_list))
        self.assertEqual(p['holdings']['citations'][0]['account_id'],'SOURCE')
        data=inputs();data['holdings']['citations']=({'existing':'source'},);data['sector_allocation']['citations']=()
        p=normalize_collected_report_holdings(db,'FILTERED','USD',data)
        self.assertEqual(p['holdings']['citations'][0],{'existing':'source'})
        self.assertEqual(p['holdings']['citations'][-1]['account_id'],'SOURCE')
        self.assertEqual(data['holdings']['citations'],({'existing':'source'},))
        self.assertEqual(data['sector_allocation']['citations'],())
    def test_filtered_source_requires_approved_definition(self):
        db=MagicMock();db.execute.return_value.fetchone.side_effect=[{'subject_type':'filtered_portfolio','source_account_id':None},None]
        with self.assertRaisesRegex(ValueError,'approved FX source'):normalize_collected_report_holdings(db,'FILTERED','USD',inputs())
    def test_filtered_source_fallback(self):
        db=MagicMock();db.execute.return_value.fetchone.side_effect=[{'subject_type':'filtered_portfolio','source_account_id':None},{'source_account_id':'APPROVED'}]
        db.execute.return_value.fetchall.return_value=fx()
        normalize_collected_report_holdings(db,'FILTERED','USD',inputs())
        self.assertEqual(db.execute.call_args.args[1],('APPROVED','2026-09-30'))
    def test_missing_subject_rejected(self):
        db=MagicMock();db.execute.return_value.fetchone.return_value=None
        with self.assertRaisesRegex(ValueError,'not registered'):normalize_collected_report_holdings(db,'UNKNOWN','USD',inputs())

if __name__=='__main__':unittest.main()
