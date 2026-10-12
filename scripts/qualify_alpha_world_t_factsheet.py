"""Local-only T template preview. Synthetic input is explicitly marked, never released."""
import argparse
from datetime import date,timedelta
import json
import math
from pathlib import Path

from ibkr_etl.alpha_world_t_analytics import methodology_identity
from ibkr_etl.hibor import expected_fixing_dates,period_average
from ibkr_etl.monthly_artifacts import render_factsheet


def synthetic_snapshot(period_end='2026-09-30'):
    """Layout exercise, NOT T historical data or actual provider fixings."""
    start,end=date(2025,1,20),date.fromisoformat(period_end)
    days=[]; cursor=start
    while cursor<=end:
        if cursor.weekday()<5: days.append(cursor)
        cursor+=timedelta(days=1)
    rows=[]
    for i,day in enumerate(days):
        # Distinct deterministic toy full-account/secondary/market/FX paths;
        # fixed units, component-wise HKD conversion, non-interest cash 58.
        fx=1+.00002*i
        gc=(1+.0007*i+.035*math.sin(i/13))*fx
        ustb=(1+.0009*i+.055*math.sin(i/19))*fx
        hsi=1+.0003*i+.025*math.sin(i/17)
        spy=(1+.0004*i+.02*math.sin(i/23))*fx
        nav=58.+91*gc+45*ustb-49*hsi-45*spy
        rows.append({'date':day.isoformat(),'currency':'HKD','nav':nav})
    actual_end=days[-1]
    fixings=[{'rate_date':d.isoformat(),'annual_rate':.025+.002*math.sin(i/25)}
             for i,d in enumerate(expected_fixing_dates(start,actual_end))]
    return {'subject_id':'alpha-world-t','period_start':start.isoformat(),'period_end':period_end,
        'data_as_of':actual_end.isoformat(),'reporting_currency':'HKD',
        'performance':{'series':rows},'risk':{'risk_free':period_average(fixings,start,actual_end)},
        'methodology_identity':methodology_identity(),'publication_allowed':False,'synthetic_data':True}


def main():
    parser=argparse.ArgumentParser(description='Private reusable Alpha World T factsheet qualification')
    parser.add_argument('--template',required=True)
    parser.add_argument('--output',required=True)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--snapshot')
    source.add_argument('--synthetic-preview',action='store_true')
    parser.add_argument('--period-end',default='2026-09-30')
    args=parser.parse_args()
    snapshot=synthetic_snapshot(args.period_end) if args.synthetic_preview else json.loads(Path(args.snapshot).read_text(encoding='utf-8'))
    result=render_factsheet(snapshot,template_path=args.template,output_path=args.output)
    print(json.dumps(result,sort_keys=True))
    return 0


if __name__=='__main__': raise SystemExit(main())
