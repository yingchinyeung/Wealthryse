import sys
from pathlib import Path
import control_reporting_correction_diagnostic_20261011 as previous
c=previous.c;c.OUT=c.OUT.parent/'snapshot-order-diagnostic-v1'
c.FILES.extend([Path(__file__).name,'diagnose_reporting_legacy_order_20261011.py'])
original=c.b.corrected_template
def template(job,code):
    old=(c.ROOT/'scripts/rehearse_reporting_snapshot_migration_20261011.py').read_text(encoding='utf-8')
    new=(c.ROOT/'scripts/diagnose_reporting_legacy_order_20261011.py').read_text(encoding='utf-8')
    # Bypass the previous text substitution, retaining the same safe builder.
    if code.count(old)!=1:raise ValueError('Exact original runner required')
    return previous.original(job,code.replace(old,new))
c.b.corrected_template=template
if __name__=='__main__':
    if sys.argv[1:]!=['start']:raise ValueError('Start only')
    c.main('start')
