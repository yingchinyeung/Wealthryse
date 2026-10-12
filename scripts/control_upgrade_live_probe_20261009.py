"""Frozen read-only pre/postflight for the approved live daily catch-up."""
import sys,json
from pathlib import Path
import control_platform_assurance_probe_20261009 as probe
from deploy_viewer_private_admin_20261008 import request
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage9'
MODE=sys.argv[2] if len(sys.argv)>2 else 'preflight'
if MODE not in ('preflight','postflight'):raise ValueError('Unknown probe')
probe.OUT=ROOT/('live-cycle-'+MODE+'-v4')
if sys.argv[3:]:
    if MODE!='postflight' or sys.argv[3:] not in (['v5'],['v6']):raise ValueError('Unknown postflight attempt')
    tail=json.loads((ROOT/'benchmark-after-derived-v1/result.json').read_bytes())
    archived,_=restore_protected(tail['protected_receipt'])
    if archived!={k:v for k,v in tail.items() if k!='protected_receipt'} or not tail['passed']:raise ValueError('Passing post-derived benchmark run required')
    if sys.argv[3:] == ['v6']:
        risk=json.loads((ROOT/'risk-after-benchmark-v1/result.json').read_bytes())
        archived,_=restore_protected(risk['protected_receipt'])
        if archived!={k:v for k,v in risk.items() if k!='protected_receipt'} or not risk['passed']:raise ValueError('Passing post-benchmark risk run required')
    probe.OUT=ROOT/('live-cycle-postflight-'+sys.argv[3])
probe.request=request
def save(name,value):
    with (probe.OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-live-cycle-'+MODE+'-'+name)},f,indent=2)
probe.save=save
class Source:
    def read_text(self,encoding):
        prefix='MODE='+repr(MODE)+'\n'
        if MODE=='postflight':
            evidence=json.loads((ROOT/'live-cycle-preflight-v4/result.json').read_bytes());remote,_=restore_protected(evidence['protected_receipt'])
            if remote!={k:v for k,v in evidence.items() if k!='protected_receipt'} or evidence['status']!='Succeeded' or len(evidence['records'])!=1:raise ValueError('Preflight evidence invalid')
            before=json.loads(evidence['records'][0]['Result'])
            if not before['passed']:raise ValueError('Passing preflight required')
            prefix+='BEFORE='+repr(before)+'\n'
        return prefix+Path(__file__).with_name('probe_upgrade_live_cycle_20261009.py').read_text(encoding=encoding)
probe.SOURCE=Source()
if __name__=='__main__':probe.main(sys.argv[1])
