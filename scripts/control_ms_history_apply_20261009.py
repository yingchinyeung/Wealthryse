"""Dispatch the qualified historical cache-only update after image/rollback gates."""
import json,sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_ms_agent_tenant_20261009 as candidate
from deploy_metadata_monitor import request,BASE
from deploy_t_runtime_release_20261009 import safe_window,idle
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain
from scan_ms_presentation_worker_candidate import verify
mode='restore' if sys.argv[2:]==['restore'] else 'apply'
probe.release.WEB=candidate.release.WEB
probe.OUT=candidate.BUNDLE/('history-cache-'+mode+'-v1')
source=Path(__file__).with_name('apply_ms_history_extension_20261009.py')
plan=candidate.release.read(candidate.BASE/'history-rehearsal-v1/protected.json')
rehearsal=candidate.release.read(candidate.BUNDLE/'history-sql-rehearsal-v2/result.json')
qualified=json.loads(rehearsal['records'][0]['Result'])
if rehearsal['status']!='Succeeded' or not qualified['passed'] or qualified['rows_qualified']!=445 or not qualified['full_preimage_rollback_passed'] or qualified['blob_sha256']!=plan['blob_sha256']:raise ValueError('Full historical rehearsal required')
for folder,path in [(candidate.release.WEB,'Microsoft.App/containerApps/ibkrv2stg-web'),(candidate.BASE/'risk-worker-release-v1','Microsoft.App/jobs/ibkrv2stg-risk-snapshots')]:
    _,_,output=verify(folder);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=candidate.release.read(folder/'scan-completed.json');archived,_=restore_protected(candidate.release.read(folder/'scan-protected-receipt.json'))
    if scan!=archived or scan['image']!=image or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Exact security gate failed')
    current=request('GET',BASE+path+'?api-version=2024-03-01')
    if current['properties']['provisioningState']!='Succeeded' or current['properties']['template']['containers'][0]['image']!=image:raise ValueError('Qualified live image differs')
if mode=='restore':
    applied=candidate.release.read(candidate.BUNDLE/'history-cache-apply-v1/result.json')
    outcome=json.loads(applied['records'][0]['Result'])
    if applied['status']!='Succeeded' or not outcome['passed']:raise ValueError('Reconcile unknown apply before restore')
    plan={**plan,'after_rows_sha256':outcome['after_rows_sha256']}
class BoundSource:
    def read_text(self,encoding):return 'PLAN='+repr(plan)+'\nMODE='+repr(mode)+'\n'+source.read_text(encoding=encoding)
probe.SOURCE=BoundSource()
original_save=probe.save
def save(name,value):
    if name=='intent':
        value={**value,'database_writes':445,'scope':'Only qualified alpha-world-ms historical disposable risk caches; no source/report/approval writes','recovery_receipt':plan['protected_receipt'],'rehearsal_sha256':candidate.release.sha(candidate.BUNDLE/'history-sql-rehearsal-v2/result.json')}
        value['protected_receipt']=retain(value,'ms-history-'+mode+'-intent')
    return original_save(name,value)
probe.save=save
if __name__=='__main__':
    if sys.argv[1]=='start':
        safe_window()
        for job in ('ibkrv2stg-risk-snapshots','ibkrv2stg-derived-daily','ibkrv2stg-benchmark-daily','ibkrv2stg-account-sync-daily'):idle(job)
    probe.main(sys.argv[1])
