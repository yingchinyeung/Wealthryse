"""Read originals, validate and create only scoped recovery controls."""
import csv
import hashlib
import json
from decimal import Decimal as D
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / '.work/alpha-ms-recovery-20261007'
EXPECTED = {('27','U16088571'):'126994.832847297',('28','U16088571'):'124621.782847297',
    ('27','U16088606'):'183545.943320587',('28','U16088606'):'183955.848990173'}


def main():
    control = {'sources': [], 'positions': [], 'nav_controls': [], 'source_type': 'original_ibkr_activity_csv_control'}
    for day in ('27', '28'):
        path = Path('C:/Users/antho/Downloads/MULTI_202608' + day + '.csv')
        content = path.read_bytes()
        with path.open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.reader(f))
        blocks = []
        for line, r in enumerate(rows, 1):
            if r[:2] == ['Statement','Header']: blocks.append([])
            blocks[-1].append((line,r))
        for block in blocks:
            info = {r[2]:r[3] for n,r in block if r[:2]==['Account Information','Data'] and len(r)>3}
            account = info.get('Account')
            if account not in ('U16088571','U16088606'): continue
            period = next(r[3] for n,r in block if r[:3]==['Statement','Data','Period'])
            if period != 'August ' + day + ', 2026' or info.get('Base Currency') != 'USD':
                raise ValueError('Unexpected account/date/currency')
            instruments = {r[3]:r[5] for n,r in block if r[:2]==['Financial Instrument Information','Data'] and len(r)>5}
            nav = {r[2].strip():D(r[6]) for n,r in block if r[:2]==['Net Asset Value','Data'] and len(r)==8}
            if nav['Total'] != D(EXPECTED[day,account]): raise ValueError('NAV differs from captured Azure control')
            values = []
            for n,r in block:
                if r[:3] != ['Open Positions','Data','Summary']: continue
                if D(r[6])*D(r[7])*D(r[10]) != D(r[11]): raise ValueError('Position price reconciliation failed')
                values.append({'account_id':account,'date':'2026-08-'+day,'conid':instruments[r[5]],
                    'currency':r[4],'quantity':r[6],'price':r[10],'value':r[11],'source_line':n})
            if len(values) != (43 if account=='U16088571' else 33): raise ValueError('Holding count mismatch')
            control['positions'].extend(values)
            control['nav_controls'].append({'account_id':account,'date':'2026-08-'+day,
                'total':str(nav['Total']),'stock':str(nav['Stock']),
                'fx':{r[2]:r[3] for n,r in block if r[:2]==['Base Currency Exchange Rate','Data'] and r[2] in ('HKD','TWD','USD')}})
        digest = hashlib.sha256(content).hexdigest()
        target = FOLDER / path.name
        if target.exists():
            if target.read_bytes() != content: raise ValueError('Original source collision')
        else:
            with target.open('xb') as f:f.write(content)
        control['sources'].append({'file':path.name,'sha256':digest,'bytes':len(content)})
    data = json.dumps(control, sort_keys=True, indent=2).encode()
    target = FOLDER / 'csv-controls.json'
    if target.exists():
        if target.read_bytes()!=data: raise ValueError('Recovery controls already differ')
    else:
        with target.open('xb') as f:f.write(data)
    print(json.dumps({'status':'controls_validated','positions':len(control['positions']),'sources':control['sources']}))


if __name__=='__main__':main()
