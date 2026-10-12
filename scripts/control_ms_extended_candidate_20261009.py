import sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_platform_ms_risk_20261009 as candidate
probe.release.WEB=candidate.release.WEB
probe.SOURCE=Path(__file__).with_name('probe_ms_extended_candidate_20261009.py')
probe.OUT=candidate.BUNDLE/'candidate-ms-probe-v1'
if __name__=='__main__':
    if sys.argv[2:]==['rollback']:
        probe.ROLLBACK=True
        probe.release.IMAGE=candidate.release.IMAGE
        probe.SOURCE=probe.SOURCE.with_name('probe_platform_access_correction_20261009.py')
        probe.OUT=candidate.BUNDLE/'rollback-access-probe-v1'
    probe.main(sys.argv[1])
