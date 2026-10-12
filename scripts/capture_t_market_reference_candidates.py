"""Bounded read-only public publisher requests, preserving raw responses."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-market-reference-candidates-20261009'
URLS={
 'hsi-public-client.js':'https://www.hsi.com.hk/etc.clientlibs/hsinet/clientlibs/clientlib-react.min.d5d4394c5673c789d4636c8e7d436dd8.js',
 'nasdaq-spy-history.json':'https://api.nasdaq.com/api/quote/SPY/historical?assetclass=etf&fromdate=2025-01-20&todate=2026-09-30&limit=5000',
}

def capture(item):
    name,url=item
    result={'name':name,'url':url,'captured_at':datetime.now(timezone.utc).isoformat()}
    try:
        request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'application/json,text/javascript,*/*'})
        with urllib.request.urlopen(request,timeout=25) as response:
            raw=response.read(32*1024*1024+1)
        if len(raw)>32*1024*1024:raise ValueError('Response too large')
        with (FOLDER/name).open('xb') as f:f.write(raw)
        result.update(status='captured',bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest())
    except Exception as exc:
        result.update(status='unavailable',error_class=type(exc).__name__,http_status=getattr(exc,'code',None))
    return result

if __name__=='__main__':
    FOLDER.mkdir(exist_ok=False)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(capture,URLS.items()))
    with (FOLDER/'receipt.json').open('x') as f:json.dump(results,f,indent=2)
    print(json.dumps(results))
