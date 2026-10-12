"""Independent, bounded historical HKAB audit; no production refresh or fallback override."""
import hashlib,json,time,threading,urllib.request
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];WORK=ROOT/'.work/alpha-t-onboarding-completion-20261009'
STOP=threading.Event()
def main():
    rows=[r for r in json.loads((ROOT/'.work/alpha-world-t-accuracy-audit-20261009/live-inputs.json').read_bytes())['hkd_risk_free'] if r['series_id']=='HKAB-HIBOR-1M']
    assert len(rows)==420 and len({r['rate_date'] for r in rows})==420
    out=WORK/'hkab-originals';out.mkdir(exist_ok=False)
    def check(row):
        day=date.fromisoformat(row['rate_date']);url=f'https://www.hkab.org.hk/api/hibor?year={day.year}&month={day.month}&day={day.day}'
        if STOP.is_set():return {'date':str(day),'status':'not_requested_after_transport_failure'}
        try:
            time.sleep(.5)
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=15) as r:raw=r.read(100000)
            with (out/(str(day)+'.json')).open('xb') as f:f.write(raw)
            p=json.loads(raw)
            if p.get('ds_is_fb')!='N' or (p.get('year'),p.get('month'),p.get('day'))!=(day.year,day.month,day.day):raise ValueError('Publisher date or publication status differs')
            v=p.get('1 Month')
            if isinstance(v,bool) or not isinstance(v,(int,float,str)) or not 0<=float(v)<=30:raise ValueError('Invalid published fixing')
            actual=float(v)/100;error=abs(actual-float(row['annual_rate']))
            return {'date':str(day),'url':url,'status':'matched' if error<=1e-12 else 'mismatch','published':actual,'stored':row['annual_rate'],'error':error,'source_sha256':hashlib.sha256(raw).hexdigest()}
        except Exception as e:
            if not isinstance(e,ValueError):STOP.set()
            return {'date':str(day),'url':url,'status':'unverified','error_class':type(e).__name__}
    results=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for row in pool.map(check,rows):
            results.append(row)
            if len(results)%40==0:print(json.dumps({'processed':len(results),'matched':sum(r['status']=='matched' for r in results)}),flush=True)
    result={'status':'verified' if all(r['status']=='matched' for r in results) else 'incomplete_or_mismatch','rows':results,'database_writes':0,'independently_reobserved':sum(r['status']=='matched' for r in results)}
    with (WORK/'hkab-history-verification.json').open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'}))
if __name__=='__main__':main()
