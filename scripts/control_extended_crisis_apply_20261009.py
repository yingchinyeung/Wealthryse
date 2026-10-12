"""Exact-image, exact-preimage cache transition with verified rollback evidence."""
import json,sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_extended_crisis_identity_20261009 as candidate
from deploy_extended_crisis_identity_20261009 import gates,read as read_protected
from deploy_metadata_monitor import request,BASE
from deploy_t_runtime_release_20261009 import idle,safe_window
from run_specialist_gateway_read_verification import retain

mode='restore' if sys.argv[2:]==['restore'] else 'apply'
for kind in ('risk','app'):
    spec,image=gates(kind)
    live=request('GET',BASE+'Microsoft.App/'+spec['resource']+'?api-version=2024-03-01')
    if live['properties']['template']['containers'][0]['image']!=image or live['properties']['provisioningState']!='Succeeded':raise ValueError('Exact accepted live image required')
    record=read_protected(candidate.STAGE/(kind+'-deployment-v2'),'verified')
    if record['image']!=image or not record['passed']:raise ValueError('Deployment not verified')
candidate.configure('app')
probe.release.WEB=candidate.release.WEB
probe.OUT=candidate.STAGE/('history-cache-'+mode+'-v1')
plan=candidate.release.read(candidate.STAGE/'history-rehearsal-v2/protected.json')
if mode=='restore':
    result=candidate.release.read(candidate.STAGE/'history-cache-apply-v1/result.json')
    outcome=json.loads(result['records'][0]['Result'])
    if result['status']!='Succeeded' or not outcome['passed'] or outcome['cache_rows_updated']!=446:raise ValueError('Reconcile unknown apply outcome first')
    plan={**plan,'after_rows_sha256':outcome['after_rows_sha256']}
source=Path(__file__).with_name('apply_extended_crisis_history_20261009.py')
class Source:
    def read_text(self,encoding):return 'PLAN='+repr(plan)+'\nMODE='+repr(mode)+'\n'+source.read_text(encoding=encoding)
probe.SOURCE=Source()
original_save=probe.save
def save(name,value):
    if name=='intent':
        value={**value,'database_writes':446,'scope':'Only disposable MS hypothetical-stress caches; all actual metrics, sources, reports and approvals preserved',
               'full_preimage_recovery_receipt':plan['protected_receipt']}
        value['protected_receipt']=retain(value,'ms-crisis-identity-'+mode+'-intent')
    original_save(name,value)
probe.save=save
if __name__=='__main__':
    if sys.argv[1]=='start':
        safe_window()
        for job in ('ibkrv2stg-risk-snapshots','ibkrv2stg-benchmark-daily','ibkrv2stg-derived-daily','ibkrv2stg-account-sync-daily'):idle(job)
    probe.main(sys.argv[1])
