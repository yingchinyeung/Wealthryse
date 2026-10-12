"""Bounded publisher reads; never overwrite reference rates or financial data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from decimal import Decimal
import hashlib,json,urllib.request,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'.work/alpha-t-onboarding-completion-20261009'
URLS={
 'hkma-hibor':'https://api.hkma.gov.hk/public/market-data-and-statistics/monthly-statistical-bulletin/er-ir/hk-interbank-ir-daily?segment=hibor.fixing&pagesize=1000&sortby=end_of_day&sortorder=desc&offset=0',
 'hkma-fx':'https://api.hkma.gov.hk/public/market-data-and-statistics/monthly-statistical-bulletin/er-ir/er-eeri-daily?choose=end_of_day&from=2025-01-17&to=2026-10-07&fields=end_of_day,usd&sortby=end_of_day&sortorder=asc&pagesize=1000&offset=0',
 'hsi-owner':'https://www.hsi.com.hk/en-hk/indexes/all-indexes/index-details/?seriesCode=hsi',
 'spy-owner':'https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy'}
def capture(item):
    name,url=item
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 WealthRyse private data verification'}),timeout=25) as response:raw=response.read(8*1024*1024)
        with (WORK/(name+'-response.bin')).open('xb') as f:f.write(raw)
        return name,{'url':url,'status':'received','sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
    except Exception as e:return name,{'url':url,'status':'unavailable','error_class':type(e).__name__,'http_status':getattr(e,'code',None)}
def main():
    WORK.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:results=dict(pool.map(capture,URLS.items()))
    stored=json.loads((ROOT/'.work/alpha-world-t-accuracy-audit-20261009/live-inputs.json').read_bytes())
    for name in ('hkma-hibor','hkma-fx'):
        if results[name]['status']!='received':continue
        try:
            p=json.loads((WORK/(name+'-response.bin')).read_bytes())
            if p.get('header',{}).get('success') is not True:raise ValueError('Unsuccessful publisher result')
            rows=p['result']['records'];field='ir_1m' if name=='hkma-hibor' else 'usd'
            by_date={r['end_of_day']:Decimal(str(r[field])) for r in rows if r.get(field) is not None}
            if len(by_date)!=len(rows):raise ValueError('Missing or duplicate publisher observations')
            if name=='hkma-hibor':
                targets=[r for r in stored['hkd_risk_free'] if r['series_id']=='HKAB-HIBOR-1M']
                missing=[r['rate_date'] for r in targets if r['rate_date'] not in by_date]
                bad=[{'date':r['rate_date'],'database':r['annual_rate'],'publisher':str(by_date[r['rate_date']]/100)} for r in targets if r['rate_date'] in by_date and abs(Decimal(str(r['annual_rate']))-by_date[r['rate_date']]/100)>Decimal('1e-12')]
                results[name].update(status='verified' if not missing and not bad else 'incomplete_or_mismatch',stored_rows=len(targets),matched=len(targets)-len(missing)-len(bad),missing=missing,mismatches=bad)
            else:
                targets=[r for r in stored['broker_fx'] if r['from_currency']=='HKD' and r['to_currency']=='USD']
                differences=[{'account':r['account_id'],'date':r['rate_date'],'broker_usd_hkd':str(1/Decimal(r['rate_text'])),'hkma_usd_hkd':str(by_date[r['rate_date']]),'relative_difference':float((1/Decimal(r['rate_text']))/by_date[r['rate_date']]-1)} for r in targets if r['rate_date'] in by_date and Decimal(r['rate_text'])>0]
                results[name].update(status='cross_check_only_different_fixing_convention',compared=len(differences),missing=sorted({r['rate_date'] for r in targets if r['rate_date'] not in by_date}),maximum_relative_difference=max((abs(r['relative_difference']) for r in differences),default=None),comparisons=differences)
        except Exception as e:results[name].update(status='invalid_response',error_class=type(e).__name__)
    for name in ('hsi-owner','spy-owner'):
        if results[name]['status']=='received':
            body=(WORK/(name+'-response.bin')).read_text(encoding='utf-8',errors='replace')
            results[name]['candidate_links']=sorted(set(re.findall(r'(?:href|src)=["\']([^"\']+(?:xlsx|csv|json)[^"\']*)["\']',body)))
            results[name]['status']='page_received_history_not_verified'
    with (WORK/'publisher-checks.json').open('x',encoding='utf-8') as f:json.dump({'captured_at':datetime.now(timezone.utc).isoformat(),'database_changed':False,'results':results},f,indent=2)
    print(json.dumps({k:{a:b for a,b in v.items() if a not in ('comparisons',)} for k,v in results.items()},default=str))
if __name__=='__main__':main()
