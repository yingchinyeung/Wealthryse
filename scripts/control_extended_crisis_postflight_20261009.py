import json,sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_extended_crisis_identity_20261009 as candidate
candidate.configure('app')
probe.release.WEB=candidate.release.WEB
probe.OUT=candidate.STAGE/'history-postflight-v1'
applied=candidate.release.read(candidate.STAGE/'history-cache-apply-v1/result.json')
expected=json.loads(applied['records'][0]['Result'])
if applied['status']!='Succeeded' or not expected['passed'] or expected['cache_rows_updated']!=446:raise ValueError('Successful migration required')
history=candidate.release.read(candidate.STAGE/'history-rehearsal-v2/result.json')
expected['report_and_approval_state']=json.loads(history['records'][0]['Result'])['report_fingerprints']
source=Path(__file__).with_name('probe_extended_crisis_postflight_20261009.py')
class Source:
    def read_text(self,encoding):return 'EXPECTED='+repr(expected)+'\n'+source.read_text(encoding=encoding)
probe.SOURCE=Source()
if __name__=='__main__':probe.main(sys.argv[1])
