"""Portable all-month T release tests with frozen inputs, no private DB paths."""
import copy
import hashlib
import json
from pathlib import Path
import unittest

from ibkr_etl.alpha_world_t_presentation import build_model
from ibkr_etl.alpha_world_t_runtime import build_payload,validate_payload
from ibkr_etl.alpha_world_t_analytics import methodology_identity
from ibkr_etl.monthly_artifacts import factsheet_layout_version
from ibkr_etl.presentation_design import presentation_content_contract_version


class TReleaseTests(unittest.TestCase):
    def test_all_21_months_source_bound_core_and_extended_risk(self):
        folder=Path(__file__).parent/'t-release-fixtures/inputs'
        if not folder.exists():
            folder=Path(__file__).resolve().parents[1]/'.work/alpha-t-reporting-batch-20261009-v2/inputs'
        paths=sorted(folder.glob('*.json'))
        self.assertEqual(len(paths),21)
        for path in paths:
            with self.subTest(period=path.stem):
                s=json.loads(path.read_bytes());original=copy.deepcopy(s)
                model=build_model(s)
                self.assertTrue(model['crisis_available'])
                self.assertEqual(s,original)
                inputs={'subject_id':'alpha-world-t','subject_type':'composite_portfolio','as_of':s['period_end'],
                    'benchmark_id':None,'rows':[],'positions':[],'t_hkd_snapshot':s,
                    'risk_free':s['risk']['risk_free'],'crisis_replay':s['risk']['crisis_replay']}
                payload=build_payload(inputs)
                self.assertTrue(validate_payload(payload));self.assertFalse(payload['publication_allowed'])
                for key in ('annualized_return','annualized_volatility','sharpe_ratio','maximum_drawdown','var_95_daily','cvar_95_daily','win_rate','total_return'):
                    self.assertEqual(payload['metrics'][key],model['risk'][key])

    def test_subject_scoped_contracts(self):
        self.assertEqual(methodology_identity()['reporting_currency'],'HKD')
        self.assertIn('alpha-world-t',factsheet_layout_version('alpha-world-t'))
        self.assertEqual(factsheet_layout_version('alpha-world-ms'),'subject-completed-factsheet-model-v14r16-alpha-world-ms-owner-disclaimer')
        self.assertEqual(presentation_content_contract_version('alpha-world-t'),'alpha-world-t-v24-hkd-hibor-internal-crisis-v1')


if __name__=='__main__':unittest.main()
