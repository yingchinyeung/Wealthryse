import copy
import unittest
from ibkr_etl.alpha_world_t_crisis import compute, validate, SLEEVES, COMPONENTS
from ibkr_etl.alpha_world_ms_crisis import market_sources
from ibkr_etl.subject_onboarding import approved_reference_definitions, configuration_sha256


def inputs():
    day='2026-09-30'; days=market_sources()['calendar']
    records={'source_nav':[], 'source_positions':[], 'mappings':[], 'broker_fx':[],
        'prices':[{'symbol':'SPY','price_date':d,'price':100-i/2,'currency':'USD','price_type':'adjusted_close'} for i,d in enumerate(days)]}
    for a in SLEEVES:
        records['source_nav'].append({'subject_id':a,'snapshot_date':day,'currency':'USD','total_text':'100'})
        records['source_positions'].append({'subject_id':a,'snapshot_date':day,'conid':'804144296',
            'currency':'TWD' if a==SLEEVES[0] else 'USD','market_value_text':'2400' if a==SLEEVES[0] else '80'})
        records['broker_fx'].append({'account_id':a,'rate_date':day,'from_currency':'TWD','to_currency':'USD','rate_text':str(1/30)})
        records['mappings'].append({'subject_id':a,'holdings_as_of':day,'conid':'804144296',
            'holding_symbol':'PSKY','currency':'USD','price_type':'adjusted_close','mapping_method':'direct',
            'source_symbol':'PSKY','current_market_value':80,'opening_weight':.8})
    return {'subject_id':'alpha-world-t','reporting_currency':'HKD','requested_as_of':day,'holdings_as_of':day,
        'contract_sha256':configuration_sha256(approved_reference_definitions()['alpha-world-t']),
        'nav':100,'component_values':dict(zip((*SLEEVES,'hang-seng-price-index','spy-settlement','constant-cash'),(91,45,-49,-45,58))),
        'records':records}


class TCrisisTests(unittest.TestCase):
    def test_exact_taiwan_fx_proxy_pure_and_reproducible(self):
        source=inputs(); before=copy.deepcopy(source)
        replay=compute(source)
        self.assertEqual(validate(replay),replay)
        self.assertEqual(source,before)
        self.assertEqual(replay['observations'],121)
        self.assertEqual(replay['proxy_components'],2)
        self.assertEqual(replay['qualification_status'],'qualified_internal')
        self.assertFalse(replay['provider_prices_independently_verified'])
        self.assertEqual(replay['publication_status'],'blocked_provisional_market_data')
        self.assertEqual(replay['sleeve_evidence'][SLEEVES[0]]['components'][0]['holding_currency'],'TWD')

    def test_reject_numeric_and_provenance_tampering(self):
        replay=compute(inputs())
        for key,value in [('maximum_drawdown',-.99),('proxy_policy_version','wrong'),
                ('provider_prices_independently_verified',True),('quality_status','pass'),
                ('publication_status','allowed'),('market_source_sha256','0'*64)]:
            with self.subTest(key=key):
                changed=copy.deepcopy(replay);changed[key]=value
                with self.assertRaises(ValueError):validate(changed)

    def test_reject_wrong_contract_composition_and_dates(self):
        for key,value in [('subject_id','alpha-world-ms'),('reporting_currency','USD'),
                ('nav',101),('holdings_as_of','2026-10-01'),('contract_sha256','0'*64)]:
            with self.subTest(key=key):
                source=inputs();source[key]=value
                with self.assertRaises(ValueError):compute(source)
        source=inputs();source['component_values']['constant-cash']=59
        with self.assertRaises(ValueError):compute(source)

    def test_missing_or_duplicate_inputs_fail_closed(self):
        for table in ('prices','mappings','source_nav','source_positions','broker_fx'):
            for mode in ('missing','duplicate'):
                with self.subTest(table=table,mode=mode):
                    source=inputs();rows=source['records'][table]
                    if mode=='missing':rows.pop(0)
                    else:rows.append(copy.deepcopy(rows[0]))
                    with self.assertRaises((ValueError,KeyError)):compute(source)

    def test_unmapped_equity_cannot_be_discarded(self):
        source=inputs()
        source['records']['source_positions'].append({'subject_id':SLEEVES[0],'snapshot_date':'2026-09-30',
            'conid':'999','currency':'USD','market_value_text':'2'})
        with self.assertRaises(ValueError):compute(source)

    def test_receivable_needs_matching_original_fields(self):
        source=inputs(); row={'subject_id':SLEEVES[0],'snapshot_date':'2026-09-30','conid':'999',
            'currency':'USD','market_value_text':'2','broker_attributes':{'symbol':'X.REC','listingExchange':'CORPACT',
                'conid':'999','accountId':SLEEVES[0],'reportDate':'20260930','currency':'USD','positionValue':'2'}}
        source['records']['source_positions'].append(row)
        self.assertEqual(len(compute(source)['sleeve_evidence'][SLEEVES[0]]['receivables']),1)
        row['broker_attributes']['positionValue']='3'
        with self.assertRaises(ValueError):compute(source)

    def test_future_or_wrong_asof_and_holdings_rejected(self):
        replay=compute(inputs())
        with self.assertRaises(ValueError):validate(replay,as_of='2026-08-31')
        with self.assertRaises(ValueError):validate(replay,nav='99')
        holdings=[{'conid':k,'currency':'HKD','market_value_text':str(v)} for k,v in replay['inputs']['component_values'].items()]
        self.assertEqual(validate(replay,holdings=holdings),replay)
        holdings[0]['currency']='USD'
        with self.assertRaises(ValueError):validate(replay,holdings=holdings)


if __name__=='__main__':unittest.main()
