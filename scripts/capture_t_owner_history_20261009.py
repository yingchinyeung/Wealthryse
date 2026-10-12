"""Private source capture of the issuer's linked historical files and HKAB sample."""
import urllib.request,hashlib,json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'.work/alpha-t-onboarding-completion-20261009'
URLS={'spy-nav-history.xlsx':'https://www.ssga.com/library-content/products/fund-data/etfs/us/navhist-us-en-spy.xlsx',
 'spy-premium-history.xlsx':'https://www.ssga.com/library-content/products/fund-data/etfs/us/pdhist-us-en-spy.xlsx',
 'hkab-inception.json':'https://www.hkab.org.hk/api/hibor?year=2025&month=1&day=20'}
def get(item):
    name,url=item
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=25) as r:raw=r.read(8*1024*1024)
        with (WORK/name).open('xb') as f:f.write(raw)
        return {'file':name,'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'status':'captured','bytes':len(raw)}
    except Exception as e:return {'file':name,'url':url,'status':'unavailable','error_class':type(e).__name__}
def main():
    with ThreadPoolExecutor(max_workers=3) as p:result=list(p.map(get,URLS.items()))
    with (WORK/'owner-history-capture.json').open('x') as f:json.dump(result,f,indent=2)
    print(json.dumps(result))
if __name__=='__main__':main()
