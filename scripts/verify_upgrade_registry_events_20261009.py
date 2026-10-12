"""Retain proof of actual qualification push/pull audit ingestion, both ACRs."""
import json
from datetime import datetime,timezone
from pathlib import Path
from audit_azure_runtime import az
from run_specialist_gateway_read_verification import retain
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=Path(__file__).resolve().parents[1]/'.work'
OUT=ROOT/'platform-upgrade-20261009-stage10/registry-repository-events.json'
EXPECTED={'ibkrv2stgauhecwc4itaoo','ibkrv115rcc62xxqblay'}

def main():
    if OUT.exists():raise ValueError('Preserve existing event proof')
    for path in ('platform-upgrade-20261009-stage9/private-build-proof-v1/result.json','platform-upgrade-20261009-stage10/legacy-private-build-proof-v1/result.json'):
        proof=protected(ROOT/path)
        if not proof['passed'] or not proof['empty_cache_pull_passed']:raise ValueError('Private build evidence required')
    query="""ContainerRegistryRepositoryEvents
| where TimeGenerated between (datetime(2026-10-09T18:00:00Z) .. now())
| where Repository == 'private-path-qualification'
| summarize Events=count(),LastObserved=max(TimeGenerated),Operations=make_set(OperationName) by _ResourceId"""
    rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if {r['_ResourceId'].rsplit('/',1)[-1] for r in rows}!=EXPECTED or any(not {'Push','Pull'}<=set(json.loads(r['Operations'])) for r in rows):raise ValueError('Both registry push and pull evidence required')
    result={'passed':True,'checked_at':datetime.now(timezone.utc).isoformat(),'query':query,'events':rows,'scope':'Actual diagnostic repository push and pull event ingestion in the existing workspace; not an alert-notification delivery test.'}
    with OUT.open('x',encoding='utf-8') as f:json.dump({**result,'protected_receipt':retain(result,'upgrade-registry-events')},f,indent=2)
    print('Both registries: real push and pull events verified and retained')

if __name__=='__main__':main()
