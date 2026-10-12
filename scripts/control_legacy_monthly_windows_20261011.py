import sys
from pathlib import Path
import control_legacy_monthly_enrichment_20261011 as c
c.OUT=c.OUT.parent/'legacy-monthly-windows-v1'
c.FILES.extend(['scripts/'+Path(__file__).name,'scripts/diagnose_legacy_monthly_windows_20261011.py'])
original=c.b.corrected_template
def template(job,code):
    old=(c.ROOT/'scripts/probe_legacy_monthly_enrichment_20261011.py').read_text(encoding='utf-8')
    if code.count(old)!=1:raise ValueError('Exact read-only probe required')
    return original(job,code.replace(old,(c.ROOT/'scripts/diagnose_legacy_monthly_windows_20261011.py').read_text(encoding='utf-8')))
c.b.corrected_template=template
if __name__=='__main__':
    if sys.argv[1:]!=['start']:raise ValueError('Start only; use window reader')
    c.main('start')
