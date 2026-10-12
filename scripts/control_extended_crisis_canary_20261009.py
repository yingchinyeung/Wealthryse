import sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_extended_crisis_identity_20261009 as candidate
candidate.configure('app')
probe.release.WEB=candidate.release.WEB
probe.OUT=candidate.STAGE/'candidate-canary-v1'
probe.SOURCE=Path(__file__).with_name('probe_extended_crisis_canary_20261009.py')
if __name__=='__main__':probe.main(sys.argv[1])
