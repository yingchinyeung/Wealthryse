"""Versioned main DNS rehearsal with current 32-caller boundary and rollback."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_main_dns_20261011 as original
import seal_reporting_stage39_20261011 as checkpoint

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage41/main-dns-v1'

def sources():
    return {name:hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest() for name in
            ('control_upgrade_main_dns_stage41_20261011.py','control_upgrade_main_dns_20261011.py',
             'seal_reporting_stage39_20261011.py','probe_upgrade_dns_route_20261010.py')}

def main(stage):
    if stage=='plan':
        if OUT.exists():raise ValueError('Preserve existing rehearsal')
        baseline=original.old.protected(ROOT/'.work/platform-upgrade-20261011-stage41/private-canaries-v1/plan.json')
        boundary=checkpoint.boundary_after_retirement()
        if boundary!=baseline['boundary']:raise ValueError('Current production boundary differs')
        original.links(False);original.idle()
        original.old.save(OUT,'plan',{'boundary':boundary,'sources':sources(),'body':original.body(),
            'links':[original.link_id(z) for z in original.RECORDS],'before_health':original.health(),
            'scope':'Three reversible main-network DNS links for isolated canary acceptance. Public fallback, credentials and normal workload definitions remain unchanged.',
            'rollback':'Remove only exact owned links with unchanged target VNet binding; retain private endpoints, zones and records.',
            'all_caller_application_acceptance':False,'platform_secret_refresh_verified':False,'cold_registry_pull_verified':False})
        print('Protected current 32-caller DNS plan');return
    if stage not in ('preview','apply','verify','rollback','verify-rollback'):raise ValueError('Unsupported operation')
    # The preserved earlier controller owns the exact link-only mutations and
    # rollback guards. Use the reviewed post-retirement caller count, never a
    # general count bypass or source-file rewrite.
    original.OUT=OUT
    original.boundary=checkpoint.boundary_after_retirement
    original.hashes=sources
    original.main(stage)

if __name__=='__main__':main(*sys.argv[1:])
