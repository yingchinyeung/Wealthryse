"""Reuse the frozen SELECT-only control boundary for duplicate provenance."""
import hashlib,sys
from pathlib import Path
import control_report_handover_schema_20261011 as c
from read_report_handover_schema_20261011 import verify
c.OUT=c.OUT.parent/'duplicates-v1'

def sources():
    return {n:hashlib.sha256((c.ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (
        Path(__file__).name,'probe_report_handover_duplicates_20261011.py','control_report_handover_schema_20261011.py','read_report_handover_schema_20261011.py')}

original=c.b.corrected_template
def template(job,code):
    return original(job,(c.ROOT/'scripts/probe_report_handover_duplicates_20261011.py').read_text())

if __name__=='__main__':
    c.b.corrected_template=template;c.sources=sources;c.execution_env=verify
    c.main(sys.argv[1])
