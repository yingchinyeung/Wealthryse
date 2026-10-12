"""Bounded per-month receipts avoid the platform's 16 KiB log-line limit."""
import sys
from pathlib import Path
import control_reporting_history_readiness_20261011 as c
c.OUT=c.OUT.parent/'historical-readiness-v2'
c.FILES.extend([Path(__file__).name,'read_reporting_history_readiness_v2_20261011.py'])
original=c.b.corrected_template
def template(job,code):
    anchor="    print('REPORTING_READINESS_FINAL '+json.dumps("
    fragment="'unready':[r for r in results if not r['monthly_available']]"
    if code.count(anchor)!=1 or code.count(fragment)!=1:raise ValueError('Exact bounded-receipt anchors required')
    code=code.replace(anchor,"    for entry in results:\n        if not entry['monthly_available']:print('REPORTING_READINESS_ITEM '+canonical_json(entry),flush=True)\n"+anchor)
    code=code.replace(fragment,"'unready_count':sum(not r['monthly_available'] for r in results), 'unready':[]")
    return original(job,code)
c.b.corrected_template=template
if __name__=='__main__':
    if sys.argv[1:]!=['start']:raise ValueError('Use the bounded reader for results')
    c.main('start')
