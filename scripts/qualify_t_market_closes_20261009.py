"""Private cross-publisher close check; rejects a stale/empty comparison quote."""
from datetime import datetime,timezone
from decimal import Decimal as D
from html.parser import HTMLParser
import hashlib,json,urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-market-reference-candidates-20261009'

class Rows(HTMLParser):
    def __init__(self):
        super().__init__();self.rows=[];self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.row=[]
        if tag in ('td','th') and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(''.join(self.cell).strip());self.cell=None
        if tag=='tr' and self.row is not None:
            self.rows.append(self.row);self.row=None

def main():
    url='https://chartexchange.com/symbol/nyse-spy/historical/'
    target=FOLDER/'chartexchange-spy-history.html'
    if not target.exists():
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=25) as r:
            raw=r.read(8*1024*1024)
        with target.open('xb') as f:f.write(raw)
        with (FOLDER/'chartexchange-spy-history.receipt.json').open('x') as f:
            json.dump({'url':url,'sha256':hashlib.sha256(raw).hexdigest(),
                       'captured_at':datetime.now(timezone.utc).isoformat()},f,indent=2)
    parsed=Rows();parsed.feed(target.read_text(encoding='utf-8'))
    candidates=[r for r in parsed.rows if r and r[0]=='2026-04-20']
    if len(candidates)!=1 or candidates[0][4]!='708.72':
        raise ValueError('Independent exception close not confirmed by parsed table')
    day_row=candidates[0]
    n=json.loads((FOLDER/'nasdaq-spy-history-full-v2.json').read_bytes())
    if n['status']['rCode']!=200 or len(n['data']['tradesTable']['rows'])!=n['data']['totalRecords']:
        raise ValueError('Incomplete Nasdaq history')
    nasdaq={datetime.strptime(r['date'],'%m/%d/%Y').date().isoformat():r for r in n['data']['tradesTable']['rows']}
    h=json.loads((FOLDER/'hsi-public-history.json').read_bytes())
    if h['code']!='0' or len(h['data'])!=1 or h['data'][0]['indexCode']!='00001.00':
        raise ValueError('HSI publisher identity differs')
    hsi={r['date']:D(r['close']) for r in h['data'][0]['valueHistory']}
    src=ROOT/'.work/alpha-world-t-accuracy-audit-20261009/live-inputs.json'
    inputs=json.loads(src.read_bytes());results=[];exceptions=[]
    for row in inputs['market_prices']:
        day=row['price_date']
        if not '2025-01-17'<=day<='2026-09-30':continue
        key=row['instrument_id'];reference=None;publisher=None
        if key=='hang-seng-price-index':reference=hsi.get(day);publisher='HSI public price-index history'
        elif key=='spy-settlement':
            quote=nasdaq.get(day)
            if quote:
                if quote['volume']=='N/A' and len({quote[k] for k in ('open','high','low','close')})==1:
                    # Do not silently accept a filled-forward empty trading-day quote.
                    if day!='2026-04-20':raise ValueError('Unexpected empty-volume quote: '+day)
                    previous=nasdaq['2026-04-17']['close']
                    if quote['close']!=previous:raise ValueError('Unexpected Nasdaq exception shape')
                    reference=D(day_row[4]);publisher='ChartExchange public daily close'
                    exceptions.append({'date':day,'nasdaq_quote':quote,'previous_close':previous,
                        'resolution':'Excluded flat OHLC with missing volume; independent daily table agrees with stored price',
                        'fallback_url':url,'fallback_row':day_row,
                        'caution':'Evidence suggests a carried-forward Nasdaq row; publisher cause is not confirmed'})
                else:reference=D(quote['close'].replace('$','').replace(',',''));publisher='Nasdaq Close/Last'
        else:continue
        difference=None if reference is None else abs(D(str(row['price']))-reference)
        results.append({'instrument':key,'date':day,'stored':row['price'],'publisher':publisher,
            'reference':str(reference) if reference is not None else None,
            'absolute_difference':str(difference) if difference is not None else None,
            'matched':difference is not None and difference<D('.005')})
    bad=[r for r in results if not r['matched']]
    proof={'status':'matched_with_documented_provider_exception' if not bad else 'incomplete',
           'source_capture_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),
           'comparison_precision':'0.005 absolute; source float32 versus published two-decimal closes',
           'counts':{key:sum(r['instrument']==key for r in results) for key in ('hang-seng-price-index','spy-settlement')},
           'mismatches_or_missing':bad,'provider_exceptions':exceptions,'comparisons':results,
           'source_sha256':{name:hashlib.sha256((FOLDER/name).read_bytes()).hexdigest() for name in
                ('nasdaq-spy-history-full-v2.json','hsi-public-history.json','chartexchange-spy-history.html')},
           'database_changed':False,'production_feed_enabled':False,'crisis_prices_verified':False}
    with (FOLDER/'qualified-closes.json').open('x') as f:json.dump(proof,f,indent=2)
    print(json.dumps({k:v for k,v in proof.items() if k not in ('comparisons','provider_exceptions')}))

if __name__=='__main__':main()
