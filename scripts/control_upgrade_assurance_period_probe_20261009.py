"""Exact scanned aligned-period validator through the real five-account tools."""
import hashlib,json,sys
import control_upgrade_assurance_candidate_20261009 as probe
from release_upgrade_assurance_period_20261009 import OUT,MODULE,CURRENT
from scan_ms_presentation_worker_candidate import verify
from rehearse_ms_worker_image_rollback import restore_protected
probe.OUT=OUT;probe.FOLDER=OUT/'read-only-v1'
def candidate():
    _,_,output=verify(OUT);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=json.loads((OUT/'scan-completed.json').read_bytes());remote,_=restore_protected(json.loads((OUT/'scan-protected-receipt.json').read_bytes()))
    if scan!=remote or scan['image']!=image or scan['assessment']['status']!='pass':raise ValueError('Exact protected scan required')
    expected={name:hashlib.sha256((OUT/'context'/name if name==MODULE else CURRENT/name).read_bytes()).hexdigest() for name in probe.FILES}
    return image,expected
probe.candidate=candidate
if __name__=='__main__':probe.main(sys.argv[1])
