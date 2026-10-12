"""Successor to the no-write failed preflight; preserve role separation."""
import hashlib,sys
from pathlib import Path
import control_report_handover_migration_20261011 as c
c.STAGE=c.ROOT/'.work/platform-upgrade-20261011-stage44/migration-v2'
original=c.b.corrected_template
original_hashes=c.hashes
def hashes():
    result=original_hashes()
    for name in (Path(__file__).name,'handover_role_boundary_20261011.py'):result['scripts/'+name]=hashlib.sha256((c.ROOT/'scripts'/name).read_bytes()).hexdigest()
    return result
def template(job,code):
    runtime=(c.ROOT/c.FILES[1]).read_text()
    if code.count(runtime)!=1 or runtime.count("if __name__=='__main__':")!=1:raise ValueError('Exact frozen runtime required')
    qualified=runtime.split("if __name__=='__main__':")[0]+(c.ROOT/'scripts/handover_role_boundary_20261011.py').read_text()
    return original(job,code.replace(runtime,qualified))
if __name__=='__main__':
    c.hashes=hashes;c.b.corrected_template=template;c.main(*sys.argv[1:])
