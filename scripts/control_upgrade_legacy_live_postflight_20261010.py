"""Read-only exact data preservation after legacy availability upgrade."""
import sys
import control_upgrade_legacy_recovery_probe_20261009 as probe
from deploy_upgrade_legacy_ha_20261010 import OUT, load

if not load('verify-ha')['passed']:
    raise ValueError('Healthy installed HA required')
probe.OUT = OUT / 'postflight-v1'
if __name__ == '__main__':
    probe.main(sys.argv[1])
