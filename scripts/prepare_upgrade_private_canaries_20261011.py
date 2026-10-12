"""Read-only plan of bounded secret-resolution canaries; no secret values saved."""
import hashlib,json
from pathlib import Path
from urllib.parse import urlsplit
import seal_reporting_stage39_20261011 as checkpoint
import control_upgrade_main_dns_20261011 as main_dns
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage41/private-canaries-v1'

def summarize(resources):
    groups={};unsupported=[]
    for item in resources:
        p=item['properties'];cfg=p.get('configuration') or {}
        env=p.get('environmentId') or p.get('managedEnvironmentId')
        identity=item.get('identity') or {}
        ids=identity.get('userAssignedIdentities') or {}
        if identity.get('type')!='UserAssigned' or len(ids)!=1:
            unsupported.append({'workload':item['name'],'reason':'Identity requires separate acceptance'});continue
        assigned=next(iter(ids));registry=cfg.get('registries') or []
        if any(set(r)-{'server','identity','username','passwordSecretRef'} for r in registry):
            raise ValueError('Unreviewed registry configuration fields')
        if any(r.get('username') or r.get('passwordSecretRef') or r.get('identity','').lower()!=assigned.lower() for r in registry):
            unsupported.append({'workload':item['name'],'reason':'Registry authentication requires separate acceptance'});continue
        key=(env,assigned,json.dumps(registry,sort_keys=True))
        group=groups.setdefault(key,{'environment':env,'identity':assigned,'registries':registry,
                                     'workloads':[],'references':{},'images':set(),'profile':p.get('workloadProfileName')})
        if group['profile']!=p.get('workloadProfileName'):raise ValueError('Mixed profile requires separate grouping')
        group['workloads'].append(item['name'])
        for container in p['template']['containers']:
            group['images'].add(container['image'])
        for secret in cfg.get('secrets') or []:
            url=secret.get('keyVaultUrl')
            if secret.get('value') or not url:
                unsupported.append({'workload':item['name'],'reason':'Non-vault secret metadata requires separate acceptance','alias':secret['name']});continue
            parsed=urlsplit(url);parts=parsed.path.strip('/').split('/')
            if parsed.scheme!='https' or parsed.hostname not in main_dns.EXPECTED or parsed.query or parsed.fragment or parsed.username or parsed.port or len(parts) not in (2,3) or parts[0]!='secrets':
                raise ValueError('Unreviewed Key Vault reference; value omitted')
            if secret.get('identity','').lower()!=assigned.lower():raise ValueError('Secret identity differs from assigned identity')
            ref=(url,assigned)
            group['references'].setdefault(ref,[]).append({'workload':item['name'],'alias':secret['name']})
    result=[]
    for index,(_,group) in enumerate(sorted(groups.items()),1):
        refs=[{'keyVaultUrl':url,'identity':identity,'consumers':sorted(consumers,key=lambda x:(x['workload'],x['alias']))}
              for (url,identity),consumers in sorted(group['references'].items())]
        result.append({**group,'group':index,'images':sorted(group['images']),'workloads':sorted(group['workloads']),'references':refs})
    return result,unsupported

def main():
    if OUT.exists():raise ValueError('Preserve previous plan')
    prior=protected(ROOT/'.work/platform-upgrade-20261011-stage40/closeout.json')
    current=checkpoint.boundary_after_retirement()
    if current!=prior['boundary']:raise ValueError('Configuration drift since protected checkpoint')
    resources=main_dns.old.current_resources()
    if main_dns.old.bindings(resources)!=current['workloads']:raise ValueError('Workload changed during inventory')
    groups,unsupported=summarize(resources)
    if sum(len(g['workloads']) for g in groups)+len({u['workload'] for u in unsupported if u['reason'].startswith(('Identity','Registry'))})!=35:
        raise ValueError('Incomplete workload inventory')
    main_dns.links(False);health=main_dns.health()
    save(OUT,'plan',{'source_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'boundary':current,'website':health,'groups':groups,'unsupported':unsupported,
        'workloads':35,'main_network_workloads':32,'secret_values_read':0,
        'secret_values_retained':False,'canaries_created':0,'network_changed':False,
        'scope':'Plan only. Proposed new manual canaries with existing managed identities and Key Vault references; no normal application, database, broker or publishing commands.',
        'requirements':['Fresh canary resources resolve existing Key Vault references without logging values.',
            'Verify exact private DNS and TLS peers with saved DNS links and public fallback retained.',
            'Do not infer cold image-pull proof from a cached image.',
            'Archive canary definitions and results, then remove only exact owned temporary resources.',
            'Do not close public access before service-specific acceptance and recovery are verified.'],
        'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','full_upgrade_complete':False})
    print(json.dumps({'workloads':35,'groups':len(groups),'references':sum(len(g['references']) for g in groups),'unsupported':unsupported,'canaries_created':0}))

if __name__=='__main__':main()
