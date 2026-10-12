"""Compare independent public closes; retain actual differences, never rewrite prices."""
from datetime import datetime
from decimal import Decimal
import hashlib,json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-market-reference-candidates-20261009'

def capture(name,url):
    target=FOLDER/name
    if target.exists():return json.loads(target.read_bytes())
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=25) as response:raw=response.read(16*1024*1024)
    result=json.loads(raw)
    with target.open('xb') as f:f.write(raw)
    with (FOLDER/(name+'.receipt.json')).open('x') as f:
        json.dump({'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)},f,indent=2)
    return result

def main():
    nasdaq=json.loads((FOLDER/'nasdaq-spy-history.json').read_bytes())
    inception=capture('nasdaq-spy-inception-v2.json',
        'https://api.nasdaq.com/api/quote/SPY/historical?assetclass=etf&fromdate=2025-01-17&todate=2025-01-20&limit=10')
    rows=[]
    for source in (nasdaq,inception):
        if source['status']['rCode']!=200:raise ValueError('Nasdaq response failed')
        source_rows=source['data']['tradesTable']['rows'] or []
        if source['data']['totalRecords']!=len(source_rows):raise ValueError('Nasdaq response incomplete')
        rows.extend(source_rows)
    prices={datetime.strptime(r['date'],'%m/%d/%Y').date().isoformat():Decimal(r['close'].replace('$','').replace(',','')) for r in rows}
    if len(prices)!=len(rows):raise ValueError('Duplicate Nasdaq date')
    stored=json.loads((ROOT/'.work/alpha-world-t-accuracy-audit-20261009/live-inputs.json').read_bytes())
    targets=[r for r in stored['market_prices'] if r['instrument_id']=='spy-settlement' and '2025-01-17'<=r['price_date']<='2026-09-30']
    compared=[];missing=[]
    for row in targets:
        day=row['price_date']
        if day not in prices:missing.append(day);continue
        close=prices[day];difference=abs(Decimal(str(row['price']))-close)
        # Yahoo publishes float32 prices; comparison at the publisher's cent precision.
        compared.append({'date':day,'stored':row['price'],'nasdaq':str(close),
                         'absolute_difference':str(difference),'matched_at_cent_precision':difference<Decimal('0.005')})
    result={'source':'Nasdaq daily Close/Last, USD, unadjusted','rows':len(targets),
            'matched':sum(r['matched_at_cent_precision'] for r in compared),'missing':missing,
            'mismatches':[r for r in compared if not r['matched_at_cent_precision']],
            'comparisons':compared,'database_changed':False}
    with (FOLDER/'spy-independent-comparison.json').open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='comparisons'}),flush=True)
    # Exact public endpoint used by the publisher's own unauthenticated client.
    metadata=capture('hsi-public-series.json',
        'https://www.hsi.com.hk/api/wsit-hsi-ddoc-ea-public-website-proxy/v1/product-data/pub/series/metadata/v2?data=values&seriesCode=hsi&language=en-hk')
    print(json.dumps(metadata)[:7000],flush=True)

if __name__=='__main__':main()
