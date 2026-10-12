"""Two bounded public checks for an independent crisis-period price source."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib,json,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-crisis-reference-access-20261009'
URLS={
 'nasdaq-aapl.json':'https://api.nasdaq.com/api/quote/AAPL/historical?assetclass=stocks&fromdate=2008-09-15&todate=2009-03-09&limit=5000',
 'stooq-aapl.csv':'https://stooq.com/q/d/l/?s=aapl.us&i=d&d1=20080915&d2=20090309',
}
def check(item):
    name,url=item
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=25) as r:raw=r.read(2*1024*1024)
        with (FOLDER/name).open('xb') as f:f.write(raw)
        return {'name':name,'url':url,'status':'received','sha256':hashlib.sha256(raw).hexdigest(),
                'bytes':len(raw),'preview':raw[:300].decode('utf-8',errors='replace')}
    except Exception as exc:
        return {'name':name,'url':url,'status':'unavailable','error_class':type(exc).__name__,'http_status':getattr(exc,'code',None)}
if __name__=='__main__':
    FOLDER.mkdir(exist_ok=False)
    with ThreadPoolExecutor(max_workers=2) as p:result=list(p.map(check,URLS.items()))
    with (FOLDER/'receipt.json').open('x') as f:json.dump({'at':datetime.now(timezone.utc).isoformat(),'results':result},f,indent=2)
    print(json.dumps(result))
