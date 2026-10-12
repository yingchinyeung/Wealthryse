"""Offline display release settings and runtime proof rejection tests."""
import copy,json,sys,unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'.work/platform-upgrade-20261009-v1/deployment-controls-tests')]
import deploy_platform_risk_labels_20261009 as deploy
import test_web_deployment as baseline

class Settings(baseline.DeploymentInvariantTests):
 def setUp(self):
  super().setUp()
  self.web['properties']['template']['containers'][0]['probes'][0]['httpGet']['path']='/readyz'
  self.scope=patch.object(baseline,'deploy',deploy);self.scope.start();self.addCleanup(self.scope.stop)
 def test_rejects_readiness_regression(self):
  deploy.readiness_probe(self.web)['httpGet']['path']='/healthz'
  with self.assertRaises(ValueError):deploy.invariant(self.web)

class RuntimeEnvelope(unittest.TestCase):
 def setUp(self):
  self.value={'status':'Succeeded','image':'qualified','job_settings_preserved':True,'records':[{'Result':'{"passed":true}'}]}
 def read(self):
  with patch.object(deploy.release,'read',return_value=self.value):
   return deploy.record('unused',expected_image='qualified')
 def test_pass(self):self.assertEqual(self.read(),{'passed':True})
 def test_reject_wrong_or_missing_proof(self):
  for key,value in [('status','Failed'),('image','other'),('job_settings_preserved',False),('records',[])]:
   with self.subTest(key=key):
    original=copy.deepcopy(self.value);self.value[key]=value
    with self.assertRaises(ValueError):self.read()
    self.value=original

if __name__=='__main__':unittest.main()
