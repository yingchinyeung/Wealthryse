import copy,sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'.work/platform-upgrade-20261009-stage5/runtime/src'),str(ROOT/'.work/platform-upgrade-20261009-v1/deployment-controls-tests')]
import deploy_platform_ms_risk_20261009 as deploy
import test_web_deployment as baseline
import apply_ms_cache_extension_20261009 as transition

class Settings(baseline.DeploymentInvariantTests):
    def setUp(self):
        super().setUp();self.web['properties']['template']['containers'][0]['probes'][0]['httpGet']['path']='/readyz'
        scope=patch.object(baseline,'deploy',deploy);scope.start();self.addCleanup(scope.stop)
    def test_readiness_regression(self):
        deploy.readiness_probe(self.web)['httpGet']['path']='/healthz'
        with self.assertRaises(ValueError):deploy.invariant(self.web)

class Transition(unittest.TestCase):
    def setUp(self):
        self.old={'payload_json':{'metrics':{'total_return':.2}},'payload_sha256':'old','generated_at':'unchanged'}
        self.payload={'metrics':{'total_return':.2,'extended':-.3}}
        self.package={'old_row':copy.deepcopy(self.old),'candidate_payload_sha256':transition.sha(self.payload),'candidate_source_sha256':'source'}
    def test_valid_extension(self):transition.validate_transition(self.old,self.payload,'source',self.package)
    def test_full_preimage_drift(self):
        self.old['generated_at']='different'
        with self.assertRaises(ValueError):transition.validate_transition(self.old,self.payload,'source',self.package)
    def test_candidate_arithmetic_drift(self):
        self.payload['metrics']['extended']=1
        with self.assertRaises(ValueError):transition.validate_transition(self.old,self.payload,'source',self.package)
    def test_source_drift(self):
        with self.assertRaises(ValueError):transition.validate_transition(self.old,self.payload,'wrong',self.package)
    def test_core_change_rejected_even_with_matching_candidate_hash(self):
        self.payload['metrics']['total_return']=.3;self.package['candidate_payload_sha256']=transition.sha(self.payload)
        with self.assertRaises(ValueError):transition.validate_transition(self.old,self.payload,'source',self.package)

if __name__=='__main__':unittest.main()
