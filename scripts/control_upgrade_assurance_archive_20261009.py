import json,sys
from pathlib import Path
import control_platform_assurance_probe_20261009 as probe
from release_upgrade_derived_tail_20261009 import OUT
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request
from run_specialist_gateway_read_verification import retain
acceptance=protected(OUT/'installed-postflight-v1/result.json')
if acceptance['status']!='Succeeded' or not json.loads(acceptance['records'][0]['Result'])['passed']:raise ValueError('Installed cycle acceptance required')
probe.OUT=OUT/'assurance-history-v1';probe.request=request
probe.SOURCE=Path(__file__).with_name('probe_upgrade_assurance_archive_20261009.py')
def save(name,value):
    with (probe.OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-assurance-history-'+name)},f,indent=2)
probe.save=save
if __name__=='__main__':
    if sys.argv[2:]:
        if sys.argv[2:]!=['before-qualified-v2']:raise ValueError('Unknown preservation attempt')
        failure=protected(OUT/'assurance-cycle-v1/result.json')
        if failure['passed']:raise ValueError('Expected original failed assurance evidence')
        probe.OUT=OUT/'assurance-history-before-qualified-v2'
    probe.main(sys.argv[1])
