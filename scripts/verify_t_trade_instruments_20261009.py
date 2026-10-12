"""Resolve original trades without Conid against broker instrument metadata."""
import json,hashlib,xml.etree.ElementTree as ET
from pathlib import Path
from collections import defaultdict
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from ibkr_etl.parser import parse_flex_xml
from ibkr_etl.util import canonical_json,sha256_text,decimal_text,pick,parse_date_text
from decimal import Decimal as D
W=ROOT/'.work/alpha-t-onboarding-completion-20261009'
audit=json.loads((W/'broker-field-verification.json').read_bytes());byfile=defaultdict(list)
for issue in audit['issues']['trades']:
    assert set(issue['differences'])=={'conid'}
    byfile[issue['source_sha256']].append(issue)
result=[]
for sha,issues in byfile.items():
    raw=(W/'originals'/(sha+'.xml')).read_bytes();assert hashlib.sha256(raw).hexdigest()==sha
    root=ET.fromstring(raw);rows={r.row_sha256:r for r in parse_flex_xml(raw).rows if r.record_type=='Trade'}
    instruments=[e.attrib for e in root.iter('SecurityInfo')]
    for issue in issues:
        a=rows[issue['row_sha256']].attributes
        candidates={e['conid'] for e in instruments if e.get('symbol')==a.get('symbol') and e.get('currency','')==a.get('currency','') and e.get('assetCategory','')==a.get('assetCategory','') and e.get('expiry','')==a.get('expiry','') and e.get('putCall','')==a.get('putCall','') and e.get('strike','')==a.get('strike','') and (not a.get('listingExchange') or e.get('listingExchange')==a.get('listingExchange'))}
        expected=issue['differences']['conid']['database']
        # Statement-of-funds FX lines are explicitly in base currency; match
        # the original pair, trade day, side, quantity and price, not the
        # converted proceeds currency.
        funds={e.get('conid') for st in root.iter('FlexStatement') if st.get('accountId')==issue['account'] for e in st.iter('StatementOfFundsLine') if a.get('assetCategory')=='CASH' and e.get('assetCategory')=='CASH' and e.get('symbol')==a.get('symbol') and e.get('buySell')==a.get('buySell') and parse_date_text(e.get('date'))==parse_date_text(a.get('tradeDate')) and e.get('tradeQuantity') and a.get('quantity') and D(e.get('tradeQuantity'))==D(a.get('quantity')) and e.get('tradePrice') and a.get('tradePrice') and D(e.get('tradePrice'))==D(a.get('tradePrice')) and e.get('conid')}
        identity={'symbol':pick(a,'symbol'),'currency':a.get('currency') or '', 'asset_category':pick(a,'assetCategory','assetClass'),'expiry':pick(a,'expiry'),'strike':decimal_text(pick(a,'strike')) or '', 'put_call':pick(a,'putCall'),'listing_exchange':pick(a,'listingExchange','exchange')}
        derived='FLEX-'+sha256_text(canonical_json(identity))[:24]
        kind='original broker instrument' if candidates=={expected} else 'original broker statement-of-funds FX event' if funds=={expected} else 'reproduced internal instrument key, not broker Conid' if expected==derived else 'unresolved instrument mapping'
        result.append({'row_sha256':issue['row_sha256'],'source_sha256':sha,'database_conid':expected,'broker_metadata_candidates':sorted(candidates),'statement_funds_candidates':sorted(funds),'verified':candidates=={expected} or funds=={expected} or expected==derived,'identity_kind':kind})
out={'rows':len(result),'verified':sum(r['verified'] for r in result),'evidence':result,'database_changed':False,'method':'Original broker instrument metadata or exact reproduction of existing internal FLEX identity; no new IDs written'}
with (W/'trade-instrument-verification-v3.json').open('x') as f:json.dump(out,f,indent=2)
print(json.dumps({k:v for k,v in out.items() if k!='evidence'}))
