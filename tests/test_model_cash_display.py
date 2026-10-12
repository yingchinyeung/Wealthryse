import copy,unittest
from unittest.mock import MagicMock
from ibkr_etl.model_cash_display import model_cash_view,load_model_cash
from ibkr_etl.subject_onboarding import approved_reference_definitions,configuration_sha256


class ModelCashTests(unittest.TestCase):
    def fixture(self,subject='alpha-world-t',normalized=100.):
        contract=approved_reference_definitions()[subject];rows=[]
        for spec in contract['components']:
            rate=1. if spec['source_currency']=='HKD' else 7.8
            rows.append({'tenant_id':'tenant','portfolio_id':subject,'snapshot_date':'2026-09-30','component_id':spec['component_id'],
                'component_type':spec['component_type'],'direction':'long' if spec['coefficient']>0 else 'short',
                'coefficient':spec['coefficient'],'source_currency':spec['source_currency'],'base_currency':'HKD',
                'source_value':1000.,'fx_rate_to_base':rate,'base_value':1000.*rate,'normalized_value':normalized,'contribution':spec['coefficient']*normalized})
        rows.append({'tenant_id':'tenant','portfolio_id':subject,'snapshot_date':'2026-09-30','component_id':'constant-cash',
            'component_type':'cash','direction':'cash','coefficient':58.,'source_currency':'HKD','base_currency':'HKD',
            'source_value':58.,'fx_rate_to_base':1.,'base_value':58.,'normalized_value':1.,'contribution':58.})
        return {'tenant_id':'tenant','subject_id':subject,'reporting_currency':'HKD','as_of':'2026-09-30','nav':sum(r['contribution'] for r in rows),
            'components':rows,'configuration':contract,'configuration_sha256':configuration_sha256(contract)}
    def test_both_approved_models(self):
        for subject in ('alpha-world-ms','alpha-world-t'):
            result=model_cash_view(**self.fixture(subject));self.assertEqual(result['normalized_value'],58);self.assertEqual(result['unit'],'normalized NAV units');self.assertFalse(result['is_broker_balance'])
    def test_current_weight_is_not_fixed_58_percent(self):
        result=model_cash_view(**self.fixture(normalized=200));self.assertAlmostEqual(result['portfolio_normalized_nav'],142);self.assertAlmostEqual(result['nav_weight'],58/142);self.assertNotAlmostEqual(result['nav_weight'],.58)
    def reject(self,change):
        f=self.fixture();change(f)
        with self.assertRaises(ValueError):model_cash_view(**f)
    def test_wrong_subject(self):self.reject(lambda f:f.update(subject_id='other'))
    def test_contract_hash(self):self.reject(lambda f:f.update(configuration_sha256='0'*64))
    def test_contract_composition(self):
        def change(f):f['configuration']['constant_cash']=59.;f['configuration_sha256']=configuration_sha256(f['configuration'])
        self.reject(change)
    def test_wrong_currency(self):self.reject(lambda f:f.update(reporting_currency='USD'))
    def test_preinception(self):self.reject(lambda f:f.update(as_of='2024-12-31'))
    def test_nonfinite_or_nonpositive_nav(self):
        for value in (0,-1,float('nan'),float('inf')):self.reject(lambda f:f.update(nav=value))
    def test_wrong_tenant(self):self.reject(lambda f:f['components'][0].update(tenant_id='other'))
    def test_wrong_component_subject(self):self.reject(lambda f:f['components'][0].update(portfolio_id='other'))
    def test_mixed_dates(self):self.reject(lambda f:f['components'][0].update(snapshot_date='2026-09-29'))
    def test_mixed_currency(self):self.reject(lambda f:f['components'][0].update(base_currency='USD'))
    def test_missing_component(self):self.reject(lambda f:f['components'].pop())
    def test_duplicate_component(self):self.reject(lambda f:f['components'].append(copy.deepcopy(f['components'][0])))
    def test_invalid_cash(self):self.reject(lambda f:f['components'][-1].update(contribution=59.,coefficient=59.,source_value=59.,base_value=59.))
    def test_cash_not_interest_bearing(self):self.assertFalse(model_cash_view(**self.fixture())['interest_bearing'])
    def test_nav_reconciliation(self):self.reject(lambda f:f.update(nav=f['nav']+.1))
    def test_bad_fx(self):self.reject(lambda f:f['components'][0].update(fx_rate_to_base=0))
    def test_loading_queries_are_tenant_date_currency_scoped(self):
        f=self.fixture();db=MagicMock();definition={'configuration_json':f['configuration'],'configuration_sha256':f['configuration_sha256']}
        db.execute.side_effect=[MagicMock(fetchall=lambda:[definition]),MagicMock(fetchone=lambda:{'snapshot_date':f['as_of'],'total_text':str(f['nav']),'currency':'HKD'}),MagicMock(fetchall=lambda:f['components'])]
        result=load_model_cash(db,tenant_id='tenant',subject_id='alpha-world-t',reporting_currency='HKD',as_of='2026-10-01')
        self.assertEqual(result['as_of'],'2026-09-30')
        for call in db.execute.call_args_list:self.assertTrue(call.args[0].strip().startswith('SELECT'));self.assertEqual(call.args[1][0],'tenant')
        self.assertEqual(db.execute.call_args_list[-1].args[1],('tenant','alpha-world-t','HKD','2026-09-30'));db.commit.assert_not_called()
    def test_missing_definition_is_not_zero_cash(self):
        db=MagicMock();db.execute.return_value.fetchall.return_value=[]
        with self.assertRaises(ValueError):load_model_cash(db,tenant_id='tenant',subject_id='alpha-world-t',reporting_currency='HKD',as_of='2026-09-30')


if __name__=='__main__':unittest.main()
