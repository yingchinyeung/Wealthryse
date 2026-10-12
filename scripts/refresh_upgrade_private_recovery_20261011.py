"""Read-only refresh of owner private audit recovery and scoped build rights."""
import json
from datetime import datetime, timezone
from pathlib import Path
import authorize_upgrade_private_build_20261009 as build
from private_upgrade_archive_transport_20261010 import archive_tunnel
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
import seal_reporting_stage39_20261011 as checkpoint
import control_upgrade_main_dns_20261011 as network

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage40/private-recovery-refresh-v1'

def main():
    if OUT.exists():raise ValueError('Preserve previous recovery refresh')
    prior=protected(checkpoint.OUT/'closeout.json')
    if checkpoint.boundary_after_retirement()!=prior['boundary']:raise ValueError('Live boundary changed')
    network.links(False)
    rights=[]
    for legacy in (False,True):
        if legacy:build.configure_legacy()
        plan=build.load('plan');verified=build.load('verified')
        if not verified['passed'] or build.boundary()!=plan['boundary']:
            raise ValueError('Approved registry build boundary changed')
        build.check_assignment(build.request('GET',build.ASSIGNMENT+build.API))
        rights.append({'registry':build.REGISTRY_NAME,'scoped_push_pull_current':True,
                       'prior_qualification_receipt':verified['protected_receipt']})
    print(json.dumps({'current_private_build_rights_verified':2}),flush=True)
    with archive_tunnel() as route:
        historical=[]
        for relative in (
            '.work/platform-upgrade-20261009-stage9/private-build-proof-v1/result.json',
            '.work/platform-upgrade-20261009-stage10/legacy-private-build-proof-v1/result.json'):
            proof=protected(ROOT/relative)
            if not proof['passed'] or not proof['empty_cache_pull_passed'] or proof['application_started']:
                raise ValueError('Prior isolated private build proof incomplete')
            historical.append({'receipt':proof['protected_receipt'],'prior_build_and_cold_pull_passed':True,
                               'rerun_today':False})
        if route['private_https_responses']<4:raise ValueError('Private evidence route incomplete')
        route_result=dict(route)
    if checkpoint.boundary_after_retirement()!=prior['boundary']:raise ValueError('Live boundary drift during refresh')
    health=network.health()
    save(OUT,'result',{'passed':True,'checked_at':datetime.now(timezone.utc).isoformat(),
        'current_private_archive_route':route_result,'current_build_permissions':rights,
        'prior_build_execution_evidence':historical,'website':health,
        'private_archive_portfolio_files_downloaded':0,'secret_values_retained':False,
        'public_access_changed':False,'credentials_rotated':False,'production_workloads_changed':False,
        'fresh_container_apps_secret_refresh':'UNVERIFIED','fresh_container_apps_cold_pull':'UNVERIFIED',
        'public_access_cutover_authorized_by_this_proof':False,
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'private_recovery_refreshed':True,'current_registry_permissions':2,
                      'private_container_apps_cutover_ready':False,'website_healthy':health['healthy']}))

if __name__=='__main__':main()
