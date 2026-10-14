import copy,hashlib,json,math,random,statistics,unittest
from unittest.mock import MagicMock,patch
import numpy as np
from ibkr_etl import alpha_world_ms_runtime as runtime
from ibkr_etl.alpha_world_ms_analytics import analyze
from ibkr_etl.alpha_world_ms_cache import methodology_identity
from test_monthly_artifacts import _ms_snapshot

class ExtendedArithmetic(unittest.TestCase):
 def test_independent_numpy_paths_and_statistics(self):
  daily=tuple(.007*math.sin(i*.79)-.00012 for i in range(300))
  actual=runtime.extended_metrics(daily)
  rng=random.Random(int.from_bytes(hashlib.sha256(','.join(f'{r:.12g}' for r in daily).encode()).digest()[:8],'big'))
  distribution=[]
  for _ in range(5000):
   starts=[rng.randint(0,len(daily)-5) for _ in range(51)]
   returns=np.asarray([daily[start+j] for start in starts for j in range(5)][:252])
   wealth=np.r_[1.,np.cumprod(1+returns)]
   distribution.append(float(np.min(wealth/np.maximum.accumulate(wealth)-1)))
  cutoff=np.quantile(distribution,.05)
  self.assertAlmostEqual(actual['expected_maximum_drawdown_1y'],np.mean(distribution),places=12)
  self.assertAlmostEqual(actual['tail_maximum_drawdown_1y'],np.mean([r for r in distribution if r<=cutoff]),places=12)
  self.assertAlmostEqual(actual['blue_mean_daily_return'],np.mean(daily),places=15)
  self.assertAlmostEqual(actual['blue_standard_error'],np.std(daily,ddof=1)/np.sqrt(300),places=15)
  wealth=np.r_[1.,np.cumprod(1+np.asarray(daily[-252:]))]
  self.assertAlmostEqual(actual['actual_maximum_drawdown_1y'],np.min(wealth/np.maximum.accumulate(wealth)-1),places=12)
  self.assertEqual(actual['drawdown_simulation_paths'],5000)
 def test_short_history_not_full_year(self):
  result=runtime.extended_metrics((.01,-.02)*9)
  self.assertIsNone(result['actual_maximum_drawdown_1y'])
  self.assertIsNone(result['expected_maximum_drawdown_1y'])
  self.assertEqual(result['drawdown_simulation_paths'],0)
 def test_twenty_returns_simulated_not_observed_year(self):
  result=runtime.extended_metrics((.01,-.02)*10)
  self.assertIsNone(result['actual_maximum_drawdown_1y'])
  self.assertEqual(result['drawdown_simulation_paths'],5000)
 def test_invalid_returns_rejected(self):
  for sample in ((0.,),(.1,float('nan')),(True,.1),(-1.,.1),(-1.2,.1)):
   with self.subTest(sample=sample),self.assertRaises(ValueError):runtime.extended_metrics(sample)
 def test_zero_variance_and_no_drawdown(self):
  result=runtime.extended_metrics((0.,)*252)
  for name in ('expected_maximum_drawdown_1y','actual_maximum_drawdown_1y','tail_maximum_drawdown_1y','blue_standard_error'):
   self.assertEqual(result[name],0.)

class Contract(unittest.TestCase):
 def setUp(self):
  self.source=_ms_snapshot();self.source['methodology_identity']=methodology_identity()
  self.inputs={'subject_id':'alpha-world-ms','subject_type':'composite_portfolio','as_of':self.source['period_end'],
   'benchmark_id':None,'rows':[],'positions':[],'risk_free':self.source['risk']['risk_free'],
   'crisis_replay':None,'ms_hkd_snapshot':self.source}
 def test_core_identical_and_source_unmodified(self):
  before=copy.deepcopy(self.inputs);payload=runtime.build_payload(self.inputs)
  core,_,_=analyze(self.source)
  self.assertEqual({k:payload['metrics'][k] for k in core},core)
  self.assertEqual(before,self.inputs);self.assertFalse(payload['publication_allowed'])
  self.assertIn('crisis_replay',payload['unavailable_capabilities'])
  self.assertIsNone(payload['metrics']['crisis_maximum_drawdown'])
  self.assertTrue(runtime.validate_payload(payload))
 def test_foreign_subject_and_currency_rejected(self):
  for field,value in [('subject_id','alpha-world-t'),('as_of','2025-01-21'),('benchmark_id','GSPC')]:
   changed=copy.deepcopy(self.inputs);changed[field]=value
   with self.subTest(field=field),self.assertRaises(ValueError):runtime.build_payload(changed)
 def test_tampered_extended_metrics_rejected(self):
  payload=runtime.build_payload(self.inputs);payload['metrics']['blue_mean_daily_return']+=.1
  with self.assertRaises(ValueError):runtime.validate_payload(payload)
 def test_foreign_crisis_rejected(self):
  self.inputs['crisis_replay']={'calculation_version':'foreign'}
  with self.assertRaises(ValueError):runtime.build_payload(self.inputs)
 def test_missing_sessions_not_silently_filtered(self):
  self.source['performance']['series'].pop(5)
  with self.assertRaises(ValueError):runtime.build_payload(self.inputs)

