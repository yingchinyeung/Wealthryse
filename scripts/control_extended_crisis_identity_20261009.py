import hashlib,sys
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_ms_agent_tenant_20261009 as installed

ROOT=installed.ROOT
STAGE=ROOT/'.work/platform-upgrade-20261009-stage7'
probe.release.WEB=installed.release.WEB
probe.OUT=STAGE/'actual-input-probe-v1'
probe.SOURCE=STAGE/'actual-input-probe.py'

def prepare():
    if probe.SOURCE.exists():raise ValueError('Preserve frozen probe')
    overlays=[]
    for name in ('crisis_identity_policy','alpha_world_ms_crisis','alpha_world_ms_runtime','risk_snapshots','agent_tools'):
        base=installed.BUNDLE/'runtime/src/ibkr_etl'/(name+'.py')
        candidate=STAGE/'runtime/src/ibkr_etl'/(name+'.py')
        source=candidate.read_text(encoding='utf-8')
        overlays.append({'name':name,'before_sha256':hashlib.sha256(base.read_bytes()).hexdigest() if base.exists() else None,
                         'after_sha256':hashlib.sha256(source.encode()).hexdigest(),'source':source})
    source=Path(__file__).with_name('probe_extended_crisis_identity_20261009.py').read_text(encoding='utf-8')
    probe.SOURCE.write_text('OVERLAYS = '+repr(overlays)+'\n'+source,encoding='utf-8')
    print('Frozen read-only actual-input probe; installed source hashes required')

if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare()
    else:probe.main(sys.argv[1])
