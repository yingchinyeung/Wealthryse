"""Run the one-row MS cache transition only after both qualified images are live."""
import json,sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_platform_ms_risk_20261009 as candidate
from deploy_metadata_monitor import request,BASE
from rehearse_ms_worker_image_rollback import restore_protected
from scan_ms_presentation_worker_candidate import verify
probe.release.WEB=candidate.release.WEB
probe.OUT=candidate.BUNDLE/'cache-extension-apply-v1'
source=Path(__file__).with_name('apply_ms_cache_extension_20261009.py')
if sys.argv[2:]==['rollback']:
    probe.OUT=candidate.BUNDLE/'cache-extension-rollback-v1'
    source=source.with_name('rollback_ms_cache_extension_20261009.py')
plan=candidate.release.read(candidate.BUNDLE/'cache-rehearsal-v1/protected.json')
package,_=restore_protected(plan['protected_receipt'])
if not plan['passed'] or not package['temporary_table_update_and_rollback_passed']:raise ValueError('Qualified cache recovery required')
for relative,path in [('application-release-v1','Microsoft.App/containerApps/ibkrv2stg-web'),('risk-worker-release-v1','Microsoft.App/jobs/ibkrv2stg-risk-snapshots')]:
    folder=candidate.BUNDLE/relative;_,_,output=verify(folder);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=candidate.release.read(folder/'scan-completed.json');protected,_=restore_protected(candidate.release.read(folder/'scan-protected-receipt.json'))
    if scan!=protected or scan['image']!=image or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Exact scan required')
    live=request('GET',BASE+path+'?api-version=2024-03-01')
    if live['properties']['template']['containers'][0]['image']!=image or live['properties']['provisioningState']!='Succeeded':raise ValueError('Qualified image not installed')
class BoundSource:
    def read_text(self,encoding):return 'PLAN='+repr(plan)+'\n'+source.read_text(encoding=encoding)
probe.SOURCE=BoundSource()
original_save=probe.save
def save(name,value):
    if name=='intent':value={**value,'database_writes':1,'scope':'One rehearsed disposable MS risk cache row only','protected_recovery_receipt':plan['protected_receipt']}
    return original_save(name,value)
probe.save=save
if __name__=='__main__':probe.main(sys.argv[1])
