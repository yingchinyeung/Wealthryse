import copy,json,math,unittest
from pathlib import Path
from unittest.mock import patch
from ibkr_etl.alpha_world_t_presentation import build_model,extended_metrics

ROOT=Path(__file__).resolve().parents[1]

class TPresentationModelTests(unittest.TestCase):
    def fixture(self):
        p=ROOT/'.work/alpha-t-internal-crisis-20261009-v1/2026-09-30.json'
        if not p.exists():self.skipTest('Private qualification fixture unavailable')
        return json.loads(p.read_bytes())

    def test_actual_fixture_pure_crisis_and_original_kpis(self):
        s=self.fixture();before=copy.deepcopy(s);model=build_model(s)
        self.assertEqual(s,before)
        self.assertTrue(model['crisis_available'])
        self.assertEqual(model['risk']['crisis_maximum_drawdown'],s['risk']['crisis_replay']['maximum_drawdown'])
        for key,value in s['risk']['metrics'].items():self.assertEqual(model['risk'][key],value)
        for key,value in s['extended_risk']['risk'].items():
            if key in model['risk']:
                if value is None:self.assertIsNone(model['risk'][key])
                else:self.assertTrue(math.isclose(model['risk'][key],value,abs_tol=1e-12,rel_tol=0),key)

    def test_wrong_scope_and_numeric_inputs_rejected(self):
        mutations=[lambda s:s.update(subject_id='alpha-world-ms'),lambda s:s.update(reporting_currency='USD'),
            lambda s:s.update(publication_allowed=True),lambda s:s['holdings']['holdings'][0].update(currency='USD'),
            lambda s:s['holdings']['holdings'][0].update(market_value_text='999'),
            lambda s:s['month_end_performance'][0].update(portfolio=999),
            lambda s:s['monthly_reporting'].update(portfolio_monthly_return=.99),
            lambda s:s['sector_allocation']['allocation'][0].update(weight=.99),
            lambda s:s['risk']['crisis_replay'].update(maximum_drawdown=-.99)]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                s=self.fixture();mutation(s)
                with self.assertRaises(ValueError):build_model(s)

    def test_early_history_is_not_mislabeled_full_year(self):
        small=extended_metrics([.01,-.02,.03])
        self.assertIsNone(small['actual_maximum_drawdown_1y'])
        self.assertIsNone(small['expected_maximum_drawdown_1y'])
        self.assertEqual(small['drawdown_simulation_paths'],0)
        with patch('ibkr_etl.alpha_world_t_presentation._simulated_drawdown_distribution',return_value=[-.1,-.2]):
            self.assertIsNone(extended_metrics([.001]*251)['actual_maximum_drawdown_1y'])
            self.assertEqual(extended_metrics([.001]*252)['actual_maximum_drawdown_1y'],0)

if __name__=='__main__':unittest.main()
