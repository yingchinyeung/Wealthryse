import sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_platform_ms_risk_20261009 as candidate
probe.release.WEB=candidate.release.WEB
probe.SOURCE=Path(__file__).with_name('qualify_ms_risk_history_20261009.py')
probe.OUT=candidate.BUNDLE/'history-rehearsal-v1'
if __name__=='__main__':probe.main(sys.argv[1])
