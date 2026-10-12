"""Read-only source/clone comparison after isolated HA and compute rollback."""
import sys
import control_upgrade_legacy_recovery_probe_20261009 as probe
from qualify_upgrade_legacy_ha_20261009 import load,TARGET,API,request
plan=load('plan');load('restore-sku-intent')
clone=request('GET',TARGET+API)
if clone['properties']['state']!='Ready' or clone['sku']!=plan['clone_sku'] or clone['properties']['highAvailability']['mode']!='Disabled':raise ValueError('Isolated rollback state must be ready')
probe.OUT=probe.ROOT/'legacy-ha-rollback-read-v1'
if __name__=='__main__':probe.main(sys.argv[1])
