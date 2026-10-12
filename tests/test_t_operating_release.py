"""Portable operating-job acceptance with frozen, source-bound inputs."""
import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from ibkr_etl.risk_snapshots import compute_risk_snapshot, snapshot_read_status, store_risk_snapshot, RiskInputsNotReady, CALCULATION_VERSION
from ibkr_etl.util import canonical_json

class TOperatingRelease(unittest.TestCase):
    def inputs(self):
        path=Path(__file__).parent/'inputs/2026-09-30.json'
        if not path.exists():path=Path(__file__).resolve().parents[1]/'.work/alpha-t-reporting-batch-20261009-v2/inputs/2026-09-30.json'
        s=json.loads(path.read_bytes())
        return dict(subject_id='alpha-world-t',subject_type='composite_portfolio',as_of=s['period_end'],
            benchmark_id=None,rows=[],positions=[],risk_free=s['risk']['risk_free'],crisis_replay=s['risk']['crisis_replay'],t_hkd_snapshot=s)

    def test_risk_branch_never_calls_legacy_and_accepts_exact_cache(self):
        inputs=self.inputs(); c=Mock(); before=copy.deepcopy(inputs)
        with patch('ibkr_etl.risk_snapshots.portfolio_risk_metrics',side_effect=AssertionError('Legacy T path')):
            p,_,source=compute_risk_snapshot(c,tenant_id='tenant',subject_id='alpha-world-t',as_of=inputs['as_of'],loaded_inputs=(inputs,{'input_query_ms':0}))
        self.assertEqual(inputs,before);c.execute.assert_not_called();self.assertEqual(len(source),64)
        record={'payload_json':p,'payload_sha256':hashlib.sha256(canonical_json(p).encode()).hexdigest(),
                'quality_status':p['quality_status'],'comparison_version':CALCULATION_VERSION}
        self.assertEqual(snapshot_read_status(record,subject_id='alpha-world-t',as_of=p['as_of']),'current')
        p['metrics']['total_return']=.9
        with self.assertRaises(RiskInputsNotReady):store_risk_snapshot(c,tenant_id='tenant',subject_id='alpha-world-t',as_of=p['as_of'],payload=p,timings={},source_data_sha256=source)
        c.execute.assert_not_called()

    def test_derived_dispatch_is_t_only_and_does_not_promote(self):
        import run_self_service_derived_refresh as entry
        c=Mock();c.execute.return_value.fetchone.return_value={'latest':'2026-09-30'}
        with patch('ibkr_etl.alpha_world_t_analytics.require_contract') as contract, patch.object(entry,'build_risk_snapshot',return_value={'status':'pass'}) as risk:
            result=entry._refresh_analytics_if_approved(c,{'subject_id':'alpha-world-t'},tenant_id='tenant')
        contract.assert_called_once_with(c,tenant_id='tenant')
        self.assertEqual(risk.call_args.kwargs['subject_id'],'alpha-world-t')
        self.assertFalse(result['onboarding_activated']);self.assertFalse(result['acceptance_check_changed'])
        c.commit.assert_not_called()

if __name__=='__main__':unittest.main()
