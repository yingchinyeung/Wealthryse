import copy
import unittest
from uuid import UUID
from decimal import Decimal
from datetime import date
from ibkr_etl.alpha_world_ms_crisis import (market_sources,align,compute,validate,
    verify_sleeve,qualified_snapshot,SLEEVES,SOURCE_VERSION,database_records)
from ibkr_etl.subject_onboarding import approved_reference_definitions,configuration_sha256


class MsCrisisTests(unittest.TestCase):
    def test_typed_database_records_preserve_exact_financial_values(self):
        identifier=UUID('00000000-0000-0000-0000-000000000001')
        row={'tenant_id':identifier,'snapshot_date':date(2026,9,30),'total_text':Decimal('123.4567890123456789')}
        self.assertEqual(database_records([row]),[{'tenant_id':str(identifier),'snapshot_date':'2026-09-30','total_text':'123.4567890123456789'}])
        self.assertIsInstance(row['tenant_id'],UUID)
        with self.assertRaises(TypeError):database_records([{'value':object()}])
    def records(self):
        days=market_sources()['calendar'];day='2026-09-30'
        records={'source_nav':[],'source_positions':[],'mappings':[],'replays':[],
                 'source_fx':[],'prices':[{'symbol':'X','price_date':d,'price':100-i/2,
                     'currency':'USD','price_type':'adjusted_close'} for i,d in enumerate(days)]}
        for s in SLEEVES:
            records['source_nav'].append({'subject_id':s,'snapshot_date':day,'currency':'USD','total_text':'100'})
            records['source_positions'].append({'subject_id':s,'snapshot_date':day,'conid':'1','currency':'USD','market_value_text':'80'})
            records['mappings'].append({'subject_id':s,'holdings_as_of':day,'conid':'1','currency':'USD',
                'price_type':'adjusted_close','mapping_method':'direct','source_symbol':'X',
                'current_market_value':80,'opening_weight':.8})
            records['replays'] += [{'subject_id':s,'holdings_as_of':day,'observation_date':d,
                'calculation_version':SOURCE_VERSION,'quality_status':'pass','normalized_value':1-.004*i,
                'drawdown':-.004*i} for i,d in enumerate(days)]
        return records
    def inputs(self):
        r=self.records()
        return {'subject_id':'alpha-world-ms','reporting_currency':'HKD',
            'contract_sha256':configuration_sha256(approved_reference_definitions()['alpha-world-ms']),
            'holdings_as_of':'2026-09-30','requested_as_of':'2026-09-30','nav':100,
            'component_values':dict(zip((*SLEEVES,'hang-seng-price-index','spy-settlement','constant-cash'),(91,45,-49,-45,58))),
            'sleeves':{s:verify_sleeve(r,s,'2026-09-30',market_sources()['calendar']) for s in SLEEVES}}
    def test_reproducible_independent_qualified_hkd(self):
        p=compute(self.inputs());self.assertEqual(validate(p),p)
        self.assertEqual(p['observations'],121);self.assertTrue(p['independent_decimal_verified'])
        self.assertLess(p['maximum_drawdown'],0)
        self.assertEqual(p['reporting_currency'],'HKD')
    def test_tampered_numeric_or_proof_rejected(self):
        p=compute(self.inputs())
        for field,value in [('maximum_drawdown',-.99),('market_source_sha256','0'*64),('qualification_status','pass')]:
            q=copy.deepcopy(p);q[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate(q)
        q=self.inputs();q['sleeves'][SLEEVES[0]]['daily'][1]['value']=.4
        with self.assertRaisesRegex(ValueError,'price proof'):compute(q)
    def test_invalid_composition_currency_or_future_holdings_rejected(self):
        for key,value in [('reporting_currency','USD'),('nav',99),('holdings_as_of','2026-10-01'),('contract_sha256','0'*64)]:
            q=self.inputs();q[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):compute(q)
        q=self.inputs();q['component_values']['constant-cash']=59
        with self.assertRaises(ValueError):compute(q)
    def test_legacy_drift_rebuilt_not_overwritten(self):
        r=self.records();before=copy.deepcopy(r);r['replays'][1]['normalized_value']+=.001
        q=verify_sleeve(r,SLEEVES[0],'2026-09-30',market_sources()['calendar'])
        self.assertGreater(q['legacy_maximum_absolute_difference'],.0009)
        self.assertEqual(q['daily'][1]['value'],.996)
        self.assertEqual(r['prices'],before['prices'])
    def test_incomplete_prices_mapping_and_fx_fail_closed(self):
        for issue in ('price','mapping','fx'):
            r=self.records()
            if issue=='price':r['prices'].pop()
            if issue=='mapping':r['mappings'].pop(0)
            if issue=='fx':r['source_positions'][0]['currency']='HKD'
            with self.subTest(issue=issue),self.assertRaises(ValueError):verify_sleeve(r,SLEEVES[0],'2026-09-30',market_sources()['calendar'])
    def test_calendar_closure_not_missing_session(self):
        obs=[{'date':'2009-01-23','price':100}]
        self.assertEqual(align(obs,['2009-01-28'],'price',hong_kong_sessions=True)[0]['2009-01-28'],100)
        with self.assertRaises(ValueError):align(obs,['2009-01-29'],'price',hong_kong_sessions=True)
        with self.assertRaises(ValueError):align([{'date':'2009-01-29','price':100}],['2009-01-28'],'price')
    def test_new_snapshot_preserves_calculations_and_original(self):
        inputs=self.inputs();replay=compute(inputs)
        p={'subject_id':'alpha-world-ms','reporting_currency':'HKD','publication_allowed':False,
           'data_as_of':'2026-09-30','snapshot_version':'v1','risk':{'metrics':{'return':.3}},
           'holdings':{'holdings':[{'conid':k,'currency':'HKD','market_value_text':str(v)} for k,v in inputs['component_values'].items()]},
           'portfolio_summary':{'latest_nav':{'total_text':'100'}},
           'publication_blockers':['primary_benchmark:not_licensed_for_publication','risk:crisis_replay_unavailable']}
        before=copy.deepcopy(p);new=qualified_snapshot(p,replay)
        self.assertEqual(before,p);self.assertEqual(p['risk']['metrics'],new['risk']['metrics'])
        self.assertFalse(new['publication_allowed']);self.assertEqual(len(new['publication_blockers']),1)

if __name__=='__main__':unittest.main()
