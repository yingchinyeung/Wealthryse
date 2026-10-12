import sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_extended_crisis_identity_20261009 as candidate
candidate.configure('app')
probe.release.WEB=candidate.release.WEB
probe.OUT=candidate.STAGE/'history-sql-rehearsal-v1'
plan=candidate.release.read(candidate.STAGE/'history-rehearsal-v2/protected.json')
source=Path(__file__).with_name('rehearse_extended_crisis_transition_20261009.py')
class Source:
    def read_text(self,encoding):return 'PLAN='+repr(plan)+'\n'+source.read_text(encoding=encoding)
probe.SOURCE=Source()
if __name__=='__main__':probe.main(sys.argv[1])
