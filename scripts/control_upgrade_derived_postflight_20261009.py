"""Independent consumer/source acceptance after installed normal daily cycle."""
import sys,json
from pathlib import Path
import control_platform_assurance_probe_20261009 as probe
from control_upgrade_legacy_recovery_probe_20261009 import protected
from release_upgrade_derived_tail_20261009 import OUT
from deploy_viewer_private_admin_20261008 import request
from run_specialist_gateway_read_verification import retain
for kind in ('derived','risk'):
    if not protected(OUT/('installed-'+kind+'-v1/result.json'))['passed']:raise ValueError('Installed ordered cycle must pass')
before=protected(OUT.parents[1]/'platform-upgrade-20261009-stage9/live-cycle-preflight-v4/result.json')
probe.OUT=OUT/'installed-postflight-v1';probe.request=request
def save(name,value):
    with (probe.OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-derived-postflight-'+name)},f,indent=2)
probe.save=save
class Source:
    def read_text(self,encoding):
        if before['status']!='Succeeded' or len(before['records'])!=1:raise ValueError('Original protected preflight required')
        return 'MODE="postflight"\nBEFORE='+repr(json.loads(before['records'][0]['Result']))+'\n'+Path(__file__).with_name('probe_upgrade_live_cycle_20261009.py').read_text(encoding=encoding)
probe.SOURCE=Source()
if __name__=='__main__':probe.main(sys.argv[1])
