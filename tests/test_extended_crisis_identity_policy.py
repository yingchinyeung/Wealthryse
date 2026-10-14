import copy
import unittest
from ibkr_etl.crisis_identity_policy import apply_identity_proxy, VERSION
from ibkr_etl import alpha_world_ms_crisis as ms
from test_alpha_world_ms_crisis import MsCrisisTests


class ExtendedIdentityPolicyTests(unittest.TestCase):
    def row(self):
        return {'conid':'804144296', 'holding_symbol':'PSKY', 'source_symbol':'PSKY',
                'mapping_method':'direct', 'current_market_value':80., 'opening_weight':.8}

    def test_both_approved_subjects_and_tickers(self):
        for subject in ('U16088571', 'alpha-world-ms'):
            for ticker in ('PSKY', 'SKYD'):
                row=self.row();row.update(holding_symbol=ticker,source_symbol=ticker)
                original=copy.deepcopy(row)
                result=apply_identity_proxy(row,subject_id=subject,crisis_start='2008-09-15')
                self.assertEqual(result['source_symbol'],'SPY')
                self.assertEqual(result['mapping_method'],'market_proxy')
                self.assertEqual(result['proxy_policy_version'],VERSION)
                self.assertEqual(result['original_source_symbol'],ticker)
                self.assertEqual((result['current_market_value'],result['opening_weight']),(80.,.8))
                self.assertEqual(row,original)
                self.assertEqual(apply_identity_proxy(result,subject_id=subject,crisis_start='2008-09-15'),result)

    def test_unapproved_subjects_and_invalid_identity_fail_closed(self):
        for subject in ('alpha-world-t','U16088606','hong-kong-china',None):
            with self.assertRaises(ValueError):
                apply_identity_proxy(self.row(),subject_id=subject,crisis_start='2008-09-15')
        row=self.row();row['holding_symbol']='AAPL'
        with self.assertRaises(ValueError):
            apply_identity_proxy(row,subject_id='alpha-world-ms',crisis_start='2008-09-15')

    def test_other_security_and_post_listing_unchanged(self):
        row=self.row();row['conid']='other'
        self.assertEqual(apply_identity_proxy(row,subject_id='alpha-world-ms',crisis_start='2008-09-15'),row)
        row=self.row()
        self.assertEqual(apply_identity_proxy(row,subject_id='alpha-world-ms',crisis_start='2025-08-07'),row)
        with self.assertRaises(ValueError):
            apply_identity_proxy(row,subject_id='alpha-world-ms',crisis_start='not-a-date')

    def records(self):
        records=MsCrisisTests().records();days=ms.market_sources()['calendar']
        for name in ('source_positions','mappings'):
            for row in records[name]:
                if row['subject_id']=='U16088571':
                    row['conid']='804144296'
                    if name=='mappings':row.update(holding_symbol='PSKY',source_symbol='PSKY')
        records['prices'] += [{'symbol':symbol,'price_date':day,'price':100-i*decline,
            'currency':'USD','price_type':'adjusted_close'}
            for symbol,decline in (('PSKY',.5),('SPY',1/6)) for i,day in enumerate(days)]
        return records

    def test_replay_substitutes_prices_not_weights_or_original_records(self):
        records=self.records();before=copy.deepcopy(records);days=ms.market_sources()['calendar']
        old=ms.verify_sleeve(records,'U16088571','2026-09-30',days)
        new=ms.verify_sleeve(records,'U16088571','2026-09-30',days,identity_proxy=True)
        self.assertAlmostEqual(old['daily'][-1]['value'],.52)
        self.assertAlmostEqual(new['daily'][-1]['value'],.84)
        self.assertEqual(new['proxy_components'],1)
        self.assertEqual(new['direct_components'],0)
        self.assertEqual(records,before)
        self.assertNotEqual(old['source_sha256'],new['source_sha256'])

    def test_missing_spy_never_falls_back_to_unverified_direct_history(self):
        records=self.records();records['prices']=[r for r in records['prices'] if r['symbol']!='SPY']
        with self.assertRaisesRegex(ValueError,'Incomplete source replay prices'):
            ms.verify_sleeve(records,'U16088571','2026-09-30',ms.market_sources()['calendar'],identity_proxy=True)

    def test_new_composite_and_legacy_report_validation_both_deterministic(self):
        legacy=ms.compute(MsCrisisTests().inputs())
        self.assertEqual(ms.validate(legacy),legacy)
        inputs=MsCrisisTests().inputs();records=self.records();days=ms.market_sources()['calendar']
        inputs['identity_proxy_policy_version']=VERSION
        inputs['sleeves']={s:ms.verify_sleeve(records,s,'2026-09-30',days,identity_proxy=True) for s in ms.SLEEVES}
        result=ms.compute(inputs)
        self.assertEqual(result['calculation_version'],ms.IDENTITY_VERSION)
        self.assertEqual(ms.validate(result),result)
        altered=copy.deepcopy(inputs)
        altered['sleeves']['U16088571']['identity_proxy_evidence']['effective_mappings'][0]['source_symbol']='PSKY'
        with self.assertRaisesRegex(ValueError,'proxy evidence'):ms.compute(altered)


if __name__=='__main__':unittest.main()
