"""Reconcile legacy details to exact frozen aggregates, still SELECT-only QA."""
import sys
from pathlib import Path
import control_reporting_history_20261011 as c
from control_upgrade_legacy_recovery_probe_20261009 import protected
original=c.b.corrected_template
c.OUT=c.qa.OUT.parent/'historical-reconciled-v1'
c.FILES.extend(['scripts/'+Path(__file__).name,'scripts/legacy_reporting_reconciliation_20261011.py','scripts/test_legacy_reporting_reconciliation_20261011.py'])
def template(job,code):
    previous=protected(c.OUT.parent/'historical-diagnostic-read-v1/result.json')
    if previous['blockers']!={'Available classification omits report holdings':20}:raise ValueError('Unexpected legacy diagnosis')
    helper=(c.ROOT/'scripts/legacy_reporting_reconciliation_20261011.py').read_text(encoding='utf-8')
    code='exec('+repr(helper)+',globals())\n'+code
    anchor="                if sid in subjects[:3]:"
    addition="""                if sid=='U16088571' and 'classifications' not in p['sector_allocation'] and 'allocation_detail' not in p['sector_allocation']:
                    from ibkr_etl.agent_tools import _sector_allocation
                    detail=_sector_allocation(Adapter(),sid,p['data_as_of'],{}).as_dict()['data']
                    p['sector_allocation']=reconcile_legacy_sector(p,detail)
                    provenance=p['sector_allocation']['classification_detail_provenance']
                    p['source_citations']['sector_allocation'].append({'table':'instrument_classifications','method':provenance['method'],'reference_sha256':provenance['reference_sha256'],'checked_at':provenance['reference_checked_at']})
                    item['legacy_summary_reconciled']=True
"""+anchor
    if code.count(anchor)!=1 or code.count('candidate-stage34-historical-plan-v1/inventory.json')!=1:raise ValueError('Exact reconciliation anchors required')
    return original(job,code.replace(anchor,addition).replace('candidate-stage34-historical-plan-v1/inventory.json','candidate-stage34-historical-reconciled-v1/inventory.json'))
c.b.corrected_template=template
if __name__=='__main__':c.main(*sys.argv[1:])
