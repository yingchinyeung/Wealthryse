"""Read current workload identities and allowlisted entrypoints, never secrets."""
import hashlib,json,re
from pathlib import Path
import control_upgrade_corrected_reports_20261011 as base
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=base.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage34/consumer-map-v1'
def main():
    if OUT.exists():raise ValueError('Preserve earlier map')
    baseline=protected(OUT.parent/'resume-v1/result.json')
    resources=base.b.current_resources()
    if base.b.bindings(resources)!=baseline['boundary']['workloads']:raise ValueError('Workload drift')
    rows=[]
    for item in resources:
        containers=item['properties']['template']['containers']
        entries=[]
        for container in containers:
            for value in container.get('args',[]):
                if isinstance(value,str) and re.fullmatch(r'scripts/[a-zA-Z0-9_]+\.py',value):entries.append(value)
        rows.append({'name':item['name'],'type':item['type'],'images':[c['image'] for c in containers],
                     'entrypoints':entries,'trigger':item['properties']['configuration'].get('triggerType')})
    save(OUT,'result',{'passed':True,'workloads':rows,'live_bindings_unchanged':True,'no_resolved_credentials_read':True,'deployment_complete':False})
    print(json.dumps(rows))
if __name__=='__main__':main()
