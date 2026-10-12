"""Archive public historical hedge prices and official HKMA USD/HKD fixings.

No account details are transmitted and no database writes occur.
"""
import hashlib
import json
import math
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import yfinance as yf

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT/'.work/alpha-ms-crisis-release-20261008/public-sources-v1'
START = '2008-09-05'
END = '2009-03-09'


def save(name, data):
    path = FOLDER/name
    with path.open('xb') as stream:stream.write(data)
    return hashlib.sha256(data).hexdigest()


def main():
    if FOLDER.exists():raise ValueError('Captured sources exist; preserve them and inspect')
    FOLDER.mkdir()
    url = 'https://api.hkma.gov.hk/public/market-data-and-statistics/monthly-statistical-bulletin/er-ir/er-eeri-daily?'+urlencode({
        'choose':'end_of_day','from':START,'to':END,'fields':'end_of_day,usd',
        'sortby':'end_of_day','sortorder':'asc','pagesize':1000,'offset':0})
    with urlopen(url,timeout=45) as response:data=response.read()
    hashes={'hkma-response.json':save('hkma-response.json',data)}
    decoded=json.loads(data)
    if not decoded.get('header',{}).get('success'):raise ValueError('HKMA source did not succeed')
    records=decoded['result']['records']
    if len(records)!=decoded['result']['datasize']:raise ValueError('HKMA response is truncated')
    fx=[]
    for row in records:
        day=row['end_of_day'];value=row['usd']
        if not START<=day<=END or isinstance(value,bool) or not math.isfinite(float(value)) or float(value)<=0:
            raise ValueError('HKMA observation is missing, out of period or invalid')
        fx.append({'date':day,'rate':float(value)})
    prices={}
    for symbol,label,currency in (('SPY','spy','USD'),('^HSI','hsi','HKD')):
        frame=yf.Ticker(symbol).history(start=START,end='2009-03-10',interval='1d',auto_adjust=False,repair=False,actions=False,timeout=30)
        hashes[label+'-raw.csv']=save(label+'-raw.csv',frame.to_csv().encode())
        rows=[]
        for index,row in frame.iterrows():
            day=index.date().isoformat();value=float(row['Close'])
            if not START<=day<=END or not math.isfinite(value) or value<=0:raise ValueError('Invalid public hedge close')
            rows.append({'date':day,'price':value})
        if not rows:raise ValueError('Public hedge source is empty')
        prices[label]={'symbol':symbol,'currency':currency,'price_type':'unadjusted_close','observations':rows}
    calendar=[r['date'] for r in prices['spy']['observations'] if r['date']>='2008-09-15']
    if len(calendar)!=121 or calendar[0]!='2008-09-15' or calendar[-1]!=END:raise ValueError('Expected crisis calendar is incomplete')
    result={'version':'ms-crisis-market-sources-v1','start':'2008-09-15','end':END,
        'calendar':calendar,'fx':{'from_currency':'USD','to_currency':'HKD','provider':'HKMA',
        'unit':'HKD per USD','source_url':url,'observations':fx},'hedges':prices,
        'provider':'Yahoo Finance via yfinance (provisional hedge prices); HKMA (official FX)',
        'retrieved_at':datetime.now(timezone.utc).isoformat(),'raw_files_sha256':hashes,
        'publication_status':'internal-review-only-no-client-distribution'}
    raw=json.dumps(result,sort_keys=True,indent=2,allow_nan=False).encode()
    digest=save('market.json',raw)
    print(json.dumps({'status':'captured','crisis_sessions':len(calendar),'fx_observations':len(fx),
         'hsi_observations':len(prices['hsi']['observations']),'sha256':digest,'database_writes':0}),flush=True)


if __name__=='__main__':main()
