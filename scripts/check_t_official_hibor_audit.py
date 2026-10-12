"""One bounded read of official HKMA data, compared without changing stored rates."""
import hashlib,json,urllib.request
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/alpha-world-t-accuracy-audit-20261009'
URL='https://api.hkma.gov.hk/public/market-data-and-statistics/monthly-statistical-bulletin/er-ir/hk-interbank-ir-daily?segment=hibor.fixing&pagesize=1000&sortby=end_of_day&sortorder=desc&offset=0'

def main():
    evidence={'url':URL,'checked_at':datetime.now(timezone.utc).isoformat(),'database_changed':False}
    try:
        with urllib.request.urlopen(urllib.request.Request(URL,headers={'User-Agent':'WealthRyse-read-only-accuracy-audit'}),timeout=30) as response:raw=response.read()
        payload=json.loads(raw)
        if payload.get('header',{}).get('success') is not True:raise ValueError('Official endpoint returned unsuccessful response')
        by_date={r['end_of_day']:r['ir_1m'] for r in payload['result']['records'] if r.get('ir_1m') is not None}
        stored=json.loads((OUT/'live-inputs.json').read_bytes())['hkd_risk_free']
        selected=[r for r in stored if r['series_id']=='HKAB-HIBOR-1M']
        missing=[r['rate_date'] for r in selected if r['rate_date'] not in by_date]
        mismatches=[{'date':r['rate_date'],'stored':r['annual_rate'],'official':float(by_date[r['rate_date']])/100}
            for r in selected if r['rate_date'] in by_date and abs(float(r['annual_rate'])-float(by_date[r['rate_date']])/100)>1e-12]
        evidence.update(status='pass' if not missing and not mismatches else 'incomplete_or_mismatch',
            stored_rows=len(selected),matched_rows=len(selected)-len(missing)-len(mismatches),missing_dates=missing,
            mismatches=mismatches,source_sha256=hashlib.sha256(raw).hexdigest())
        with (OUT/'official-hkma-response.json').open('xb') as f:f.write(raw)
    except Exception as error:
        evidence.update(status='unavailable',error_class=type(error).__name__,http_status=getattr(error,'code',None))
    with (OUT/'official-hibor-check.json').open('x',encoding='utf-8') as f:json.dump(evidence,f,indent=2)
    print(json.dumps(evidence))

if __name__=='__main__':main()
