"""Read original Azure Flex archives and reconcile privately; never call Flex or mutate DB."""
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import hashlib, json, re, sys, threading, time
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'.work/alpha-ms-azure-controller-runtime')]
from ibkr_etl.parser import parse_flex_xml
from ibkr_etl.util import pick, parse_date_text, decimal_text
WORK=ROOT/'.work/alpha-t-onboarding-completion-20261009'
ACCOUNTS={'U16088571','U16088606'}

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,indent=2,default=str)

def capture():
    from azure.identity import AzureCliCredential
    from azure.storage.blob import BlobServiceClient
    class Credential:
        def __init__(self):self.inner=AzureCliCredential();self.lock=threading.Lock();self.token=None
        def get_token(self,*scopes,**kwargs):
            with self.lock:
                if self.token is None or self.token.expires_on<time.time()+120:self.token=self.inner.get_token(*scopes,**kwargs)
                return self.token
    service=BlobServiceClient('https://ibkrv115rcc62xxqblay.blob.core.windows.net',credential=Credential(),retry_total=1,connection_timeout=20,read_timeout=40)
    container=service.get_container_client('raw-statements')
    items=[{'name':b.name,'size':b.size,'etag':b.etag,'last_modified':str(b.last_modified)} for b in container.list_blobs(name_starts_with='00000000-0000-0000-0000-000000000001/') if b.name.endswith('.xml')]
    save(WORK/'archive-listing.json',{'captured_at':datetime.now(timezone.utc).isoformat(),'blobs':items})
    # Same-name hash suffixes are only a download optimization, never proof of integrity.
    selected={}
    for r in items:
        suffix=re.search(r'_([0-9a-f]{12,64})\.xml$',r['name'])
        key=(suffix.group(1),r['size']) if suffix else (r['name'],r['size'])
        selected.setdefault(key,r)
    folder=WORK/'originals';folder.mkdir(exist_ok=True)
    def get(r):
        try:
            if r['size']>64*1024*1024:raise ValueError('Archive exceeds bounded size')
            from azure.core import MatchConditions
            raw=container.get_blob_client(r['name']).download_blob(etag=r['etag'],match_condition=MatchConditions.IfNotModified).readall()
            if len(raw)!=r['size']:raise ValueError('Archive size differs')
            sha=hashlib.sha256(raw).hexdigest();path=folder/(sha+'.xml')
            with path.open('xb') as f:f.write(raw)
            return {**r,'sha256':sha,'status':'captured'}
        except Exception as e:return {**r,'status':'unavailable','error_class':type(e).__name__}
    results=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for row in pool.map(get,selected.values()):
            results.append(row)
            if len(results)%25==0:print(json.dumps({'captured_progress':len(results),'selected':len(selected)}),flush=True)
    save(WORK/'archive-capture.json',{'read_only':True,'new_flex_requests':0,'listed':len(items),'results':results})
    print(json.dumps({'listed':len(items),'selected':len(selected),'statuses':dict(Counter(r['status'] for r in results))}))

def reconcile():
    manifest=json.loads((WORK/'archive-capture.json').read_bytes())
    source=ROOT/'.work/alpha-world-t-accuracy-audit-20261009/live-inputs.json'
    data=json.loads(source.read_bytes());indices=defaultdict(dict);cash=defaultdict(dict);files=[];parse_errors=[]
    for entry in manifest['results']:
        if entry['status']!='captured':continue
        path=WORK/'originals'/(entry['sha256']+'.xml');raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=entry['sha256']:raise ValueError('Local archive integrity failure')
        try:parsed=parse_flex_xml(raw)
        except Exception as e:
            parse_errors.append({'sha256':entry['sha256'],'error_class':type(e).__name__});continue
        selected=[r for r in parsed.rows if r.account_id in ACCOUNTS]
        files.append({'sha256':entry['sha256'],'rows':len(selected),'types':dict(Counter(r.record_type for r in selected))})
        for row in selected:
            indices[row.record_type.lower()].setdefault((row.account_id,row.row_sha256),{'source_sha256':entry['sha256'],'attributes':row.attributes,'source_key':row.source_key,'date':row.report_date})
            if row.record_type.lower()=='cashtransaction':cash[(row.account_id,row.source_key)][row.row_sha256]={'source_sha256':entry['sha256'],'attributes':row.attributes,'date':row.report_date}
    comparisons={}
    for table,kinds in {'nav_details':('equitysummarybyreportdateinbase','netassetvaluebyassetclass'), 'position_details':('openposition',), 'cash_details':('cashreportcurrency','cashreport'), 'broker_fx':('conversionrate','fxrate','currencyconversionrate'), 'cash_transactions':('cashtransaction',)}.items():
        lookup={k:v for kind in kinds for k,v in indices[kind].items()};missing=[];matched=[]
        for r in data[table]:
            hit=lookup.get((r['account_id'],r['row_sha256']))
            identity={k:r.get(k) for k in ('account_id','snapshot_date','rate_date','effective_date','currency','conid','source_key','row_sha256') if k in r}
            if hit:matched.append({**identity,'source_sha256':hit['source_sha256']})
            else:missing.append(identity)
        comparisons[table]={'rows':len(data[table]),'exact_broker_attribute_hash_matches':len(matched),'missing':missing,'matched':matched}
    missing_fields=Counter();field_recovery=[];projection_mismatches=[];groups=defaultdict(list)
    for r in data['cash_transactions']:
        variants=cash.get((r['account_id'],r['source_key']),{})
        hit=variants.get(r['row_sha256'])
        if hit:
            a=hit['attributes'];tid=pick(a,'transactionID');typ=pick(a,'type','transactionType')
            missing_fields['broker_id_absent' if not tid else 'broker_id_present']+=1
            missing_fields['broker_type_absent' if not typ else 'broker_type_present']+=1
            projected={'transaction_id':tid,'transaction_type':typ,'amount_text':decimal_text(pick(a,'amount')),'description':pick(a,'description'),'effective_date':hit['date'],'settle_date':parse_date_text(pick(a,'settleDate')),'currency':pick(a,'currency')}
            different={k:{'database':r[k],'broker':v} for k,v in projected.items() if r[k]!=v}
            if different:projection_mismatches.append({'account':r['account_id'],'source_key':r['source_key'],'differences':different})
            if (tid and not r['transaction_id']) or (typ and not r['transaction_type']):field_recovery.append({'account':r['account_id'],'source_key':r['source_key'],'transaction_id':tid,'transaction_type':typ})
        groups[(r['account_id'],r['effective_date'],r['currency'],r['amount_text'],r['description'])].append((r,hit))
    repeated=[]
    for key,rows in groups.items():
        if len(rows)<2:continue
        attrs=[h['attributes'] for _,h in rows if h]
        varying={k:sorted({a.get(k,'') for a in attrs}) for k in set().union(*(a.keys() for a in attrs)) if len({a.get(k,'') for a in attrs})>1} if attrs else {}
        repeated.append({'economic_key':key,'count':len(rows),'all_originals_found':len(attrs)==len(rows),'varying_broker_fields':varying,'source_keys':[r['source_key'] for r,_ in rows], 'classification':'unresolved-economic-identity-no-deletion'})
    save(WORK/'broker-reconciliation.json',{'database_changed':False,'source_export_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'archive_files':files,'parse_errors':parse_errors,'comparisons':comparisons,'cash_original_field_counts':dict(missing_fields),'recoverable_fields':field_recovery,'projection_mismatches':projection_mismatches,'repeated_economic_groups':repeated})
    print(json.dumps({'comparisons':{k:{'rows':v['rows'],'exact_matches':v['exact_broker_attribute_hash_matches'],'missing':len(v['missing'])} for k,v in comparisons.items()},'cash_original_field_counts':dict(missing_fields),'recoverable_fields':len(field_recovery),'projection_mismatches':len(projection_mismatches),'repeated_groups':len(repeated),'duplicate_varying_fields':dict(Counter(k for r in repeated for k in r['varying_broker_fields'])),'parse_errors':len(parse_errors)}))

if __name__=='__main__':
    {'capture':capture,'reconcile':reconcile}[sys.argv[1]]()
