import json,sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_ms_agent_tenant_20261009 as candidate
probe.release.WEB=candidate.release.WEB
probe.OUT=candidate.BUNDLE/'history-postflight-v1'
applied=candidate.release.read(candidate.BUNDLE/'history-cache-apply-v1/result.json')
if applied['status']!='Succeeded':raise ValueError('Successful migration required')
expected=json.loads(applied['records'][0]['Result'])
expected['report_and_approval_state']=json.loads(candidate.release.read(candidate.BUNDLE/'live-postflight-v1/result.json')['records'][0]['Result'])['report_and_approval_state']
source=Path(__file__).with_name('probe_ms_history_postflight_20261009.py')
class BoundSource:
    def read_text(self,encoding):return 'EXPECTED='+repr(expected)+'\n'+source.read_text(encoding=encoding)
probe.SOURCE=BoundSource()
if __name__=='__main__':probe.main(sys.argv[1])
