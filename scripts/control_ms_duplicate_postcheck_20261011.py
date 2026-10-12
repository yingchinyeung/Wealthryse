"""Unchanged website identity and installed source after the scoped correction."""
import hashlib,sys
from pathlib import Path
import control_report_handover_schema_20261011 as c
from read_report_handover_schema_20261011 import verify
c.OUT=c.ROOT/'.work/platform-upgrade-20261011-stage43/ms-duplicate-postcheck-v1'

def sources():return {n:hashlib.sha256((c.ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (
    Path(__file__).name,'verify_ms_duplicate_registration_20261011.py','control_report_handover_schema_20261011.py','read_report_handover_schema_20261011.py')}

original=c.b.corrected_template
def template(job,code):
    result=c.protected(c.OUT.parent/'ms-duplicate-apply-v1/result.json')
    if not result['passed'] or result['proof']['phase']!='apply':raise ValueError('Verified correction required')
    return original(job,'import sys\nsys.path[:0]=["/app/src","/app/scripts"]\n'+(c.ROOT/'scripts/verify_ms_duplicate_registration_20261011.py').read_text())

if __name__=='__main__':
    c.b.corrected_template=template;c.sources=sources;c.execution_env=verify
    c.main(sys.argv[1])
