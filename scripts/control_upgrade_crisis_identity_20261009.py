import sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_ms_agent_tenant_20261009 as candidate
probe.release.WEB=candidate.release.WEB
probe.OUT=candidate.BUNDLE/'physical-crisis-identity-v1'
probe.SOURCE=Path(__file__).with_name('probe_upgrade_crisis_identity_20261009.py')
if __name__=='__main__':probe.main(sys.argv[1])
