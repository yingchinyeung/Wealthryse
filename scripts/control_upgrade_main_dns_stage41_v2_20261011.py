"""Current-boundary private DNS rehearsal; frozen v1 planning attempt retained."""
import hashlib,inspect,sys
from pathlib import Path
import control_upgrade_main_dns_20261011 as original
import seal_reporting_stage39_20261011 as checkpoint

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage41/main-dns-v2'

def current_boundary_function():
    source=inspect.getsource(original.boundary)
    if source.count('len(callers)!=34')!=1:raise ValueError('Original boundary contract changed')
    scope=dict(vars(original))
    exec(compile(source.replace('len(callers)!=34','len(callers)!=32'),'reviewed-current-boundary','exec'),scope)
    return scope['boundary']

def sources():
    return {name:hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest() for name in
            ('control_upgrade_main_dns_stage41_v2_20261011.py','control_upgrade_main_dns_20261011.py',
             'seal_reporting_stage39_20261011.py','test_upgrade_main_dns_stage41_20261011.py')}

def main(stage):
    current=current_boundary_function()
    if stage=='plan':
        if OUT.exists():raise ValueError('Preserve existing rehearsal')
        baseline=original.old.protected(ROOT/'.work/platform-upgrade-20261011-stage41/private-canaries-v1/plan.json')
        boundary=current()
        if boundary!=baseline['boundary']:raise ValueError('Current production boundary differs')
        original.links(False);original.idle()
        original.old.save(OUT,'plan',{'boundary':boundary,'sources':sources(),'body':original.body(),
            'links':[original.link_id(z) for z in original.RECORDS],'before_health':original.health(),
            'scope':'Three reversible main-network DNS links for isolated canary acceptance. Public fallback, credentials and normal workload definitions remain unchanged.',
            'rollback':'Remove only exact owned links with unchanged target VNet binding; retain private endpoints, zones and records.',
            'supersedes':'stage41/main-dns-v1 plan only; no Azure mutation from v1',
            'all_caller_application_acceptance':False,'platform_secret_refresh_verified':False,'cold_registry_pull_verified':False})
        print('Protected current 32-caller DNS plan v2');return
    if stage not in ('preview','apply','verify','rollback','verify-rollback'):raise ValueError('Unsupported operation')
    original.OUT=OUT
    original.boundary=current
    original.hashes=sources
    original.main(stage)

if __name__=='__main__':main(*sys.argv[1:])
