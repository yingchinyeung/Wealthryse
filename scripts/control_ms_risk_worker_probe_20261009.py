import json,sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_ms_risk_worker_20261009 as candidate
probe.release.WEB=candidate.release.WEB
fallback=sys.argv[2:]==['rollback']
probe.ROLLBACK=fallback
probe.release.IMAGE=candidate.IMAGE
probe.OUT=candidate.BUNDLE/('worker-rollback-probe-v1' if fallback else 'worker-candidate-probe-v1')
source=Path(__file__).with_name('probe_ms_risk_worker_20261009.py')
result=candidate.release.read(candidate.BUNDLE/'candidate-ms-probe-v1/result.json')
expected=json.loads(result['records'][0]['Result'])
if result['status']!='Succeeded' or not expected['passed']:raise ValueError('Actual arithmetic qualification required')
class BoundSource:
    def read_text(self,encoding):return 'EXPECTED='+repr(expected)+'\nFALLBACK='+repr(fallback)+'\n'+source.read_text(encoding=encoding)
probe.SOURCE=BoundSource()
if __name__=='__main__':probe.main(sys.argv[1])
