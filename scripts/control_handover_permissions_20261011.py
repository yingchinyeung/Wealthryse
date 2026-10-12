"""Read-only investigation of missing assumed table-level grants."""
import hashlib,sys
from pathlib import Path
import control_report_handover_migration_20261011 as c
c.STAGE=c.ROOT/'.work/platform-upgrade-20261011-stage44/permissions-v1'
original=c.b.corrected_template
def template(job,code):
    return original(job,'MIGRATION_DIGEST='+repr(c.hashes()[c.FILES[2]])+'\n'+(c.ROOT/'scripts/probe_handover_permissions_20261011.py').read_text())
original_hashes=c.hashes
def hashes():
    result=original_hashes()
    for name in (Path(__file__).name,'probe_handover_permissions_20261011.py'):result['scripts/'+name]=hashlib.sha256((c.ROOT/'scripts'/name).read_bytes()).hexdigest()
    return result
if __name__=='__main__':
    c.hashes=hashes;c.b.corrected_template=template;c.main(sys.argv[1],'preflight')
