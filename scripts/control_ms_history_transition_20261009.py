import sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_ms_agent_tenant_20261009 as candidate
probe.release.WEB=candidate.release.WEB
probe.OUT=candidate.BUNDLE/'history-sql-rehearsal-v2'
source=Path(__file__).with_name('rehearse_ms_history_transition_20261009_v2.py')
plan=candidate.release.read(candidate.BASE/'history-rehearsal-v1/protected.json')
if not plan['passed'] or plan['qualified']!=445 or not plan['protected_receipt']['protected_readback_verified']:raise ValueError('Protected history required')
class BoundSource:
    def read_text(self,encoding):return 'PLAN='+repr(plan)+'\n'+source.read_text(encoding=encoding)
probe.SOURCE=BoundSource()
if __name__=='__main__':probe.main(sys.argv[1])
