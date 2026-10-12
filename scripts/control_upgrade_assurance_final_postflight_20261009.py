"""Independent source/report preservation after qualified normal assurance."""
import json,sys
import control_upgrade_derived_postflight_20261009 as base
from release_upgrade_assurance_period_20261009 import OUT
from control_upgrade_legacy_recovery_probe_20261009 import protected
from run_specialist_gateway_read_verification import retain
run=protected(OUT/'installed-cycle-v2/result.json')
if not run['passed']:raise ValueError('All five installed assurance runs must pass')
base.probe.OUT=OUT/'final-postflight-v1'
def save(name,value):
    with (base.probe.OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-assurance-final-postflight-'+name)},f,indent=2)
base.probe.save=save
if __name__=='__main__':base.probe.main(sys.argv[1])
