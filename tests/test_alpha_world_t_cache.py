import copy
from datetime import date
import hashlib
import unittest
from unittest.mock import Mock, patch

from ibkr_etl.alpha_world_t_cache import build, canonical, validate
from test_alpha_world_t_analytics import snapshot


class TPrivateCacheTests(unittest.TestCase):
    def test_replay_is_pure_and_source_bound(self):
        source = snapshot()
        before = copy.deepcopy(source)
        record = build(source)
        self.assertTrue(validate(record, as_of=source['period_end'], current_snapshot=source))
        self.assertEqual(source, before)
        self.assertEqual(record, build(source))
        self.assertFalse(record['payload']['publication_allowed'])

    def test_hash_does_not_qualify_wrong_numbers_or_scope(self):
        mutations = [lambda p: p['metrics'].update(total_return=.99),
            lambda p: p['metrics'].update(total_return=True),
            lambda p: p['metrics'].update(crisis_maximum_drawdown=-.3),
            lambda p: p.update(publication_allowed=True),
            lambda p: p.update(production_qualified=True),
            lambda p: p.update(subject_id='alpha-world-ms'),
            lambda p: p.update(reporting_currency='USD'),
            lambda p: p['methodology_identity'].update(contract_sha256='0'*64),
            lambda p: p['analytics_evidence'].update(daily_return_observations=99),
            lambda p: p['unavailable_capabilities'].pop('crisis_replay'),
            lambda p: p['source_snapshot']['risk']['risk_free'].update(annual_rate=.04),
            lambda p: p['source_snapshot']['performance']['series'][1].update(nav=109),
            lambda p: p['source_snapshot']['performance']['series'][1].update(nav=float('nan'))]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                record = build(snapshot())
                mutation(record['payload'])
                with self.assertRaises((ValueError, KeyError)):
                    record['payload_sha256'] = hashlib.sha256(canonical(record['payload']).encode()).hexdigest()
                    validate(record, as_of='2025-01-22')

    def test_stale_dates_and_wrong_cutoffs_rejected(self):
        source = snapshot()
        record = build(source)
        with self.assertRaises(ValueError):
            validate(record, as_of='2025-01-23')
        source['period_end'] = '2025-01-23'
        with self.assertRaisesRegex(ValueError, 'latest required'):
            build(source)

    def test_same_mean_hibor_changes_invalidate_source(self):
        from ibkr_etl.hibor import period_average
        source = snapshot()
        record = build(source)
        fixings = source['risk']['risk_free']['observations']
        fixings[0]['annual_rate'] += .001
        fixings[1]['annual_rate'] -= .001
        source['risk']['risk_free'] = period_average(fixings, date(2025,1,20), date(2025,1,22))
        self.assertAlmostEqual(source['risk']['risk_free']['annual_rate'], record['payload']['metrics']['risk_free_annual_rate'])
        with self.assertRaisesRegex(ValueError, 'source values changed'):
            validate(record, as_of='2025-01-22', current_snapshot=source)

    def test_source_nav_changes_invalidate_source(self):
        source = snapshot()
        record = build(source)
        source['performance']['series'][1]['nav'] += 1
        with self.assertRaisesRegex(ValueError, 'source values changed'):
            validate(record, as_of='2025-01-22', current_snapshot=source)

    def test_production_loaded_input_bypass_is_rejected(self):
        from ibkr_etl.risk_snapshots import compute_risk_snapshot, RiskInputsNotReady
        c = Mock()
        inputs = {'subject_id':'alpha-world-t', 'as_of':'2025-01-22'}
        with patch('ibkr_etl.risk_snapshots.portfolio_risk_metrics') as legacy:
            with self.assertRaises(RiskInputsNotReady):
                compute_risk_snapshot(c, tenant_id='tenant', subject_id='alpha-world-t',
                                      as_of='2025-01-22', loaded_inputs=(inputs, {}))
            legacy.assert_not_called()
        c.execute.assert_not_called()

    def test_production_legacy_cache_read_and_write_are_rejected(self):
        from ibkr_etl.risk_snapshots import snapshot_read_status, store_risk_snapshot, RiskInputsNotReady
        c = Mock()
        self.assertEqual(snapshot_read_status({}, subject_id='alpha-world-t', as_of='2025-01-22'),
                         't_methodology_unqualified')
        with self.assertRaises(RiskInputsNotReady):
            store_risk_snapshot(c, tenant_id='tenant', subject_id='alpha-world-t',
                as_of='2025-01-22', payload={}, timings={}, source_data_sha256='0'*64)
        c.execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
