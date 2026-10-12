import sys,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'scripts'),str(ROOT/'.work/platform-upgrade-20261009-stage5b/runtime/src'),str(ROOT/'.work/platform-upgrade-20261009-v1/deployment-controls-tests')]
import deploy_ms_agent_tenant_20261009 as deploy
import test_web_deployment as baseline
class Settings(baseline.DeploymentInvariantTests):
    def setUp(self):
        super().setUp();self.web['properties']['template']['containers'][0]['probes'][0]['httpGet']['path']='/readyz'
        scope=patch.object(baseline,'deploy',deploy);scope.start();self.addCleanup(scope.stop)
    def test_readiness_not_weakened(self):
        deploy.readiness_probe(self.web)['httpGet']['path']='/healthz'
        with self.assertRaises(ValueError):deploy.invariant(self.web)
if __name__=='__main__':unittest.main()
