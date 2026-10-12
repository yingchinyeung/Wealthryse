import copy,hashlib,json,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from ibkr_etl.alpha_world_t_runtime import build_payload,validate_payload
from ibkr_etl.risk_snapshots import compute_risk_snapshot,snapshot_read_status,store_risk_snapshot,RiskInputsNotReady,CALCULATION_VERSION
from ibkr_etl.util import canonical_json

class TRuntimeTests(unittest.TestCase):
    def inputs(self):
        p=Path(__file__).resolve().parents[1]/'.work/alpha-t-internal-crisis-20261009-v1/2026-09-30.json'
        if not p.exists():self.skipTest('Private qualification fixture unavailable')
        s=json.loads(p.read_bytes())
        return {'subject_id':'alpha-world-t','subject_type':'composite_portfolio','as_of':s['period_end'],
            'benchmark_id':None,'rows':[],'positions':[],'risk_free':s['risk']['risk_free'],
            'crisis_replay':s['risk']['crisis_replay'],'t_hkd_snapshot':s}

    def record(self,payload):
        return {'payload_json':payload,'payload_sha256':hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
                'quality_status':'warn','comparison_version':CALCULATION_VERSION}

    def test_replay_and_routing_never_call_legacy_analytics(self):
        inputs=self.inputs();before=copy.deepcopy(inputs);c=Mock()
        with patch('ibkr_etl.risk_snapshots.portfolio_risk_metrics') as legacy:
            payload,_,fingerprint=compute_risk_snapshot(c,tenant_id='tenant',subject_id='alpha-world-t',
                as_of=inputs['as_of'],loaded_inputs=(inputs,{'input_query_ms':0}))
            legacy.assert_not_called();c.execute.assert_not_called()
        self.assertEqual(inputs,before);self.assertEqual(len(fingerprint),64)
        self.assertTrue(validate_payload(payload));self.assertFalse(payload['publication_allowed'])
        self.assertEqual(snapshot_read_status(self.record(payload),subject_id='alpha-world-t',as_of=inputs['as_of']),'current')
        self.assertEqual(payload['metrics']['crisis_maximum_drawdown'],inputs['crisis_replay']['maximum_drawdown'])
        store_risk_snapshot(c,tenant_id='tenant',subject_id='alpha-world-t',as_of=inputs['as_of'],
            payload=payload,timings={},source_data_sha256=fingerprint)
        c.execute.assert_called_once();self.assertIn('INSERT INTO risk_analytics_snapshots',c.execute.call_args.args[0])

    def test_rehashed_tampering_still_fails_and_no_cache_write(self):
        original=build_payload(self.inputs())
        mutations=[lambda p:p.update(reporting_currency='USD'),lambda p:p.update(publication_allowed=True),
            lambda p:p['metrics'].update(total_return=.9),lambda p:p['metrics'].update(expected_maximum_drawdown_1y=-.9),
            lambda p:p['crisis_replay'].update(provider_prices_independently_verified=True),
            lambda p:p['methodology_identity'].update(subject_id='alpha-world-ms')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                p=copy.deepcopy(original);mutate(p);c=Mock()
                self.assertEqual(snapshot_read_status(self.record(p),subject_id='alpha-world-t',as_of=p['as_of']),'t_methodology_unqualified')
                with self.assertRaises(RiskInputsNotReady):store_risk_snapshot(c,tenant_id='tenant',subject_id='alpha-world-t',as_of=p['as_of'],payload=p,timings={},source_data_sha256='0'*64)
                c.execute.assert_not_called()

    def test_incomplete_inputs_cannot_use_generic_paths(self):
        inputs=self.inputs();inputs['crisis_replay']=None
        with self.assertRaises(ValueError):build_payload(inputs)
        inputs=self.inputs();inputs['benchmark_id']='GSPC'
        with self.assertRaises(ValueError):build_payload(inputs)

if __name__=='__main__':unittest.main()
