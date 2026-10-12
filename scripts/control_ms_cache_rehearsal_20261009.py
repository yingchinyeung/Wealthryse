"""Exact qualified candidate, temporary-table-only cache rehearsal."""
import json,sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_platform_ms_risk_20261009 as candidate
probe.release.WEB=candidate.release.WEB
probe.OUT=candidate.BUNDLE/'cache-rehearsal-v1'
source=Path(__file__).with_name('rehearse_ms_cache_extension_20261009.py')
result=candidate.release.read(candidate.BUNDLE/'candidate-ms-probe-v1/result.json')
expected=json.loads(result['records'][0]['Result'])
if result['status']!='Succeeded' or not expected['passed'] or not expected['core_metrics_identical']:raise ValueError('Actual arithmetic qualification required')
class BoundSource:
    def read_text(self,encoding):return 'EXPECTED='+repr(expected)+'\n'+source.read_text(encoding=encoding)
probe.SOURCE=BoundSource()
if __name__=='__main__':probe.main(sys.argv[1])
