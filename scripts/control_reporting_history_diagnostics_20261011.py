"""Inspect legacy sector shape without relaxing the normalization gate."""
import sys
from pathlib import Path
import control_reporting_history_20261011 as c
from control_upgrade_legacy_recovery_probe_20261009 import protected
original=c.b.corrected_template
c.OUT=c.qa.OUT.parent/'historical-diagnostics-v1'
c.FILES.append('scripts/'+Path(__file__).name)

def template(job,code):
    previous=protected(c.OUT.parent/'historical-plan-v1/result.json')
    if previous['proof']['summary']['U16088571']!={'snapshots':21,'corrections':1,'blockers':20}:raise ValueError('Unexpected prior inventory')
    anchor="            except ValueError as error:item['blocker']=str(error)[:250]"
    extra="""            except ValueError as error:
                item['blocker']=str(error)[:250]
                h=original['holdings'];s=original['sector_allocation'];holdings=h.get('holdings',[])
                item['legacy_shape']={'holding_keys':sorted(h),'sector_keys':sorted(s),'holdings':len(holdings),
                    'holding_currencies':sorted({str(r.get('currency')) for r in holdings}),
                    'holding_dates':sorted({str(r.get('snapshot_date')) for r in holdings}),
                    'gross_holdings':sum(abs(float(r['market_value_text'])) for r in holdings),
                    'gross_sector':s.get('gross_market_value'),'classified_gross':s.get('classified_market_value'),
                    'available':s.get('available'),'classifications':len(s.get('classifications',[])),
                    'classification_conids_match':{str(r['conid']) for r in s.get('classifications',[])}=={str(r['conid']) for r in holdings},
                    'allocation_count':len(s.get('allocation',[])),'detail_count':len(s.get('allocation_detail',[])),
                    'allocation_gross':sum(float(r.get('market_value',0)) for r in s.get('allocation',[])),
                    'allocation_holdings':sum(int(r.get('holdings',0)) for r in s.get('allocation',[])),
                    'detail_gross':sum(float(r.get('market_value',0)) for r in s.get('allocation_detail',[])),
                    'reported_holdings':s.get('holdings'),'reported_classified_holdings':s.get('classified_holdings'),
                    'classification_coverage_pct':s.get('classification_coverage_pct')}
""".rstrip()
    if code.count(anchor)!=1 or code.count("candidate-stage34-historical-plan-v1/inventory.json")!=1:raise ValueError('Exact diagnostic anchors required')
    return original(job,code.replace(anchor,extra).replace('candidate-stage34-historical-plan-v1/inventory.json','candidate-stage34-historical-diagnostics-v1/inventory.json'))
c.b.corrected_template=template
if __name__=='__main__':c.main(*sys.argv[1:])
