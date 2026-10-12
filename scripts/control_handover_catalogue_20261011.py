"""Qualify exact old/new website images against migrated schema, SELECT-only."""
import hashlib,json,sys
from pathlib import Path
import control_report_handover_schema_20261011 as c
import scan_ms_presentation_worker_candidate as scan
from read_report_handover_schema_20261011 import verify
from rehearse_ms_worker_image_rollback import restore_protected
ROOT=c.ROOT

def main(action,kind):
    if kind not in ('fallback','candidate'):raise ValueError('Invalid image kind')
    source,_,output=scan.verify(c.release.OUT)
    image=c.release.BASE if kind=='fallback' else output['registry']+'/'+output['repository']+'@'+output['digest']
    assessment=json.loads((c.release.OUT/'scan-completed.json').read_bytes())
    archived,_=restore_protected(json.loads((c.release.OUT/'scan-protected-receipt.json').read_bytes()))
    if assessment!=archived or assessment['assessment']['status']!='pass' or assessment['assessment']['blocking_findings']!=0:raise ValueError('Protected security gate required')
    prepared=(c.release.OUT/'context/prepared.json').read_bytes()
    if hashlib.sha256(prepared).hexdigest()!=source['files_sha256']['prepared.json']:raise ValueError('Source inventory drift')
    inventory=json.loads(prepared)
    if inventory['base_image']!=c.release.BASE or inventory['candidate']!=source['candidate_hashes']:raise ValueError('Wrong image lineage')
    hashes=source['candidate_hashes'] if kind=='candidate' else inventory['baseline']
    c.OUT=ROOT/'.work/platform-upgrade-20261011-stage44'/('catalogue-'+kind+'-v1')
    c.sources=lambda:{n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'probe_handover_catalogue_20261011.py','control_report_handover_schema_20261011.py','read_report_handover_schema_20261011.py')}
    original_template=c.b.corrected_template
    def template(job,code):
        applied=c.protected(ROOT/'.work/platform-upgrade-20261011-stage44/migration-v2/schema-apply-v1/result.json')
        if not applied['passed'] or not applied['proof']['schema_committed']:raise ValueError('Verified schema apply required')
        code='import sys\nsys.path[:0]=["/app/src","/app/scripts"]\nIMAGE='+repr(image)+'\nSOURCE_HASHES='+repr(hashes)+'\n'+(ROOT/'scripts/probe_handover_catalogue_20261011.py').read_text()
        result=original_template(job,code)
        return result
    # The existing controller explicitly sets release.BASE; substitute only after
    # preserving its original boundary check and exact fallback image above.
    boundary=c.release.unchanged
    c.release.unchanged=boundary;c.release.BASE=image
    c.b.corrected_template=template;c.execution_env=verify
    c.main(action)
if __name__=='__main__':main(*sys.argv[1:])
